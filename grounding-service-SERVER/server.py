"""
Grounding Service API Server

Provides HTTP endpoint for UI element grounding using UI-Venus-Ground-7B model.
Handles concurrent requests via internal queue to prevent GPU memory conflicts.
"""

import asyncio
import base64
import logging
import os
from asyncio import Queue
from io import BytesIO
from typing import Optional

import torch
from fastapi import FastAPI, HTTPException
from PIL import Image
from pydantic import BaseModel, Field
from transformers import (
    AutoProcessor,
    AutoTokenizer,
    Qwen2_5_VLForConditionalGeneration,
)
from qwen_vl_utils import process_vision_info

# -------------------------
# LOGGING SETUP
# -------------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# -------------------------
# CONFIGURATION
# -------------------------
MODEL_NAME = "inclusionAI/UI-Venus-Ground-7B"
INFERENCE_TIMEOUT = 30.0  # seconds
MAX_QUEUE_SIZE = 100  # Prevent memory issues if requests pile up

GENERATION_CONFIG = {
    "max_new_tokens": 64,
    "do_sample": False,
    "temperature": None,
    "top_p": None,
    "top_k": None,
}

# -------------------------
# GLOBAL STATE
# -------------------------
app = FastAPI(
    title="Grounding Service",
    description="UI element grounding service using UI-Venus-Ground-7B",
    version="1.0.0"
)

# Model components (loaded once at startup)
model: Optional[Qwen2_5_VLForConditionalGeneration] = None
processor: Optional[AutoProcessor] = None
tokenizer: Optional[AutoTokenizer] = None

# Inference queue for serializing GPU access
inference_queue: Queue = Queue(maxsize=MAX_QUEUE_SIZE)

# -------------------------
# REQUEST/RESPONSE MODELS
# -------------------------
class GroundRequest(BaseModel):
    image_base64: str = Field(..., description="Base64-encoded PNG image")
    instruction: str = Field(..., description="Natural language description of target element")
    
    class Config:
        json_schema_extra = {
            "example": {
                "image_base64": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
                "instruction": "the blue 'Login' button in the top-right corner"
            }
        }


class GroundResponse(BaseModel):
    bbox: list[float] = Field(..., description="Normalized bounding box [x1, y1, x2, y2] in range [0, 1]")
    point: list[float] = Field(..., description="Normalized center point [x, y] in range [0, 1]")
    success: bool = Field(..., description="Whether grounding succeeded")
    error: Optional[str] = Field(None, description="Error message if success=False")
    
    class Config:
        json_schema_extra = {
            "example": {
                "bbox": [0.3257, 0.9600, 0.4988, 1.0000],
                "point": [0.4123, 0.9800],
                "success": True,
                "error": None
            }
        }


class HealthResponse(BaseModel):
    status: str
    gpu_available: bool
    model_loaded: bool
    queue_size: int


# -------------------------
# MODEL LOADING
# -------------------------
@app.on_event("startup")
async def load_model():
    """Load model once at startup and start inference worker"""
    global model, processor, tokenizer
    
    logger.info("=" * 60)
    logger.info("Starting Grounding Service")
    logger.info("=" * 60)
    
    try:
        logger.info(f"Loading model: {MODEL_NAME}")
        
        # Load model
        model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
            MODEL_NAME,
            device_map="auto",
            torch_dtype=torch.bfloat16,
            attn_implementation="flash_attention_2",
        ).eval()
        logger.info("✓ Model loaded")
        
        # Load tokenizer
        tokenizer = AutoTokenizer.from_pretrained(
            MODEL_NAME,
            trust_remote_code=True,
        )
        logger.info("✓ Tokenizer loaded")
        
        # Load processor
        processor = AutoProcessor.from_pretrained(
            MODEL_NAME,
            use_fast=False,
        )
        logger.info("✓ Processor loaded")
        
        # Check GPU
        if torch.cuda.is_available():
            gpu_name = torch.cuda.get_device_name(0)
            gpu_memory = torch.cuda.get_device_properties(0).total_memory / 1e9
            logger.info(f"✓ GPU: {gpu_name} ({gpu_memory:.1f} GB)")
        else:
            logger.warning("⚠ No GPU detected - inference will be slow!")
        
        # Start inference worker
        asyncio.create_task(inference_worker())
        logger.info("✓ Inference worker started")
        
        logger.info("=" * 60)
        logger.info("Service ready - listening for requests")
        logger.info("=" * 60)
        
    except Exception as e:
        logger.error(f"Failed to load model: {e}")
        raise


# -------------------------
# INFERENCE WORKER
# -------------------------
async def inference_worker():
    """
    Single worker that processes inference requests sequentially.
    This prevents concurrent GPU access which would cause memory issues.
    """
    logger.info("Inference worker ready")
    
    while True:
        try:
            # Get next request from queue
            request, response_future = await inference_queue.get()
            
            logger.debug(f"Processing: '{request.instruction[:60]}...'")
            
            try:
                # Run inference (blocking GPU operation)
                result = await asyncio.to_thread(run_inference, request)
                response_future.set_result(result)
                logger.debug("✓ Inference completed")
                
            except Exception as e:
                logger.error(f"Inference failed: {e}", exc_info=True)
                response_future.set_exception(e)
            
        except Exception as e:
            logger.error(f"Worker error: {e}", exc_info=True)
            # Keep worker alive even if something goes wrong


def run_inference(request: GroundRequest) -> GroundResponse:
    """
    Run model inference on a single request.
    This function runs in a thread pool to avoid blocking the event loop.
    """
    try:
        # Decode base64 image
        image_bytes = base64.b64decode(request.image_base64)
        image = Image.open(BytesIO(image_bytes)).convert("RGB")
        
        # Save to temporary location for model input
        # (Model expects file path, not PIL Image directly)
        temp_path = "/tmp/grounding_input.png"
        image.save(temp_path)
        
        # Build prompt
        prompt_template = (
            "Outline the position corresponding to the instruction: {}. "
            "The output should be only [x1,y1,x2,y2]."
        )
        full_prompt = prompt_template.format(request.instruction)
        
        # Prepare messages
        min_pixels = 1_500_000
        max_pixels = 2_400_000
        
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "image": temp_path,
                        "min_pixels": min_pixels,
                        "max_pixels": max_pixels,
                    },
                    {
                        "type": "text",
                        "text": full_prompt,
                    },
                ],
            }
        ]
        
        # Process with tokenizer
        text = processor.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )
        
        image_inputs, video_inputs = process_vision_info(messages)
        
        model_inputs = processor(
            text=[text],
            images=image_inputs,
            videos=video_inputs,
            padding=True,
            return_tensors="pt",
        ).to(model.device)
        
        # Generate
        with torch.inference_mode():
            generated_ids = model.generate(
                **model_inputs,
                **GENERATION_CONFIG,
            )
        
        # Decode output
        generated_ids_trimmed = [
            out_ids[len(in_ids):]
            for in_ids, out_ids in zip(
                model_inputs.input_ids,
                generated_ids,
            )
        ]
        
        output_text = processor.batch_decode(
            generated_ids_trimmed,
            skip_special_tokens=True,
            clean_up_tokenization_spaces=False,
        )
        
        # Parse and normalize coordinates
        try:
            box = eval(output_text[0])  # Model outputs "[x1, y1, x2, y2]" as string
            
            # Get input dimensions for normalization
            input_height = model_inputs["image_grid_thw"][0][1] * 14
            input_width = model_inputs["image_grid_thw"][0][2] * 14
            
            # Normalize to [0, 1]
            abs_x1 = float(box[0]) / input_width
            abs_y1 = float(box[1]) / input_height
            abs_x2 = float(box[2]) / input_width
            abs_y2 = float(box[3]) / input_height
            
            bbox = [abs_x1, abs_y1, abs_x2, abs_y2]
            
        except Exception as e:
            logger.warning(f"Failed to parse model output '{output_text[0]}': {e}")
            bbox = [0.0, 0.0, 0.0, 0.0]
        
        # Calculate center point
        point = [
            (bbox[0] + bbox[2]) / 2,
            (bbox[1] + bbox[3]) / 2,
        ]
        
        return GroundResponse(
            bbox=bbox,
            point=point,
            success=True,
            error=None
        )
        
    except Exception as e:
        logger.error(f"Inference error: {e}", exc_info=True)
        return GroundResponse(
            bbox=[0.0, 0.0, 0.0, 0.0],
            point=[0.0, 0.0],
            success=False,
            error=str(e)
        )


# -------------------------
# API ENDPOINTS
# -------------------------
@app.post("/ground", response_model=GroundResponse)
async def ground_element(request: GroundRequest):
    """
    Ground a UI element in an image.
    
    Accepts a base64-encoded image and natural language instruction,
    returns normalized bounding box and center point coordinates.
    """
    if model is None:
        raise HTTPException(status_code=503, detail="Model not loaded")
    
    # Check queue capacity
    if inference_queue.full():
        raise HTTPException(
            status_code=429,
            detail=f"Queue full ({MAX_QUEUE_SIZE} requests). Try again later."
        )
    
    logger.info(f"Request: '{request.instruction[:60]}...'")
    
    # Create future for response
    response_future = asyncio.Future()
    
    # Add to queue
    try:
        await inference_queue.put((request, response_future))
    except asyncio.QueueFull:
        raise HTTPException(status_code=429, detail="Queue full")
    
    # Wait for result with timeout
    try:
        result = await asyncio.wait_for(response_future, timeout=INFERENCE_TIMEOUT)
        logger.info(f"✓ Completed: bbox={result.bbox}, point={result.point}")
        return result
        
    except asyncio.TimeoutError:
        logger.error(f"Timeout after {INFERENCE_TIMEOUT}s")
        raise HTTPException(
            status_code=504,
            detail=f"Inference timeout after {INFERENCE_TIMEOUT}s"
        )


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """
    Health check endpoint.
    
    Returns service status, GPU availability, model load state, and queue size.
    """
    return HealthResponse(
        status="healthy" if model is not None else "loading",
        gpu_available=torch.cuda.is_available(),
        model_loaded=model is not None,
        queue_size=inference_queue.qsize()
    )


@app.get("/")
async def root():
    """Root endpoint - redirects to docs"""
    return {
        "service": "Grounding Service",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/health"
    }


# -------------------------
# ENTRY POINT
# -------------------------
if __name__ == "__main__":
    import uvicorn
    
    host = os.getenv("GROUNDING_SERVICE_HOST", "127.0.0.1")
    port = int(os.getenv("GROUNDING_SERVICE_PORT", "8000"))
    
    logger.info(f"Starting server on {host}:{port}")
    
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info"
    )
