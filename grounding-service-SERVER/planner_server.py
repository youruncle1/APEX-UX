"""
Planner Service API Server

Provides HTTP endpoint for planning using Qwen-3-VL-8B-Instruct model.
Uses Pydantic schema validation to ensure structured outputs.
"""

import asyncio
import base64
import logging
import json
from asyncio import Queue
from io import BytesIO
from typing import Optional, Literal

import torch
from fastapi import FastAPI, HTTPException
from PIL import Image
from pydantic import BaseModel, Field, ValidationError
from transformers import Qwen3VLForConditionalGeneration, AutoProcessor
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
MODEL_NAME = "Qwen/Qwen3-VL-8B-Instruct"
INFERENCE_TIMEOUT = 45.0
MAX_QUEUE_SIZE = 50

GENERATION_CONFIG = {
    "max_new_tokens": 256,
    "do_sample": True,
    "temperature": 0.7,
    "top_p": 0.8,
    "top_k": 20,
    "repetition_penalty": 1.0,
}

# -------------------------
# PYDANTIC SCHEMAS
# -------------------------
class ActionSchema(BaseModel):
    """
    Pydantic schema for action validation.
    This ensures the model output conforms to our expected structure.
    """
    action_type: Literal[
        "CLICK", "TYPE", "SCROLL_UP", "SCROLL_DOWN",
        "HOVER", "DRAG_AND_DROP", "PRESS_KEY",
        "BACK", "FORWARD", "DONE"
    ] = Field(..., description="Type of action to perform")
    
    target_description: str = Field(
        ..., 
        description="Natural language description of target element (empty string if not needed)"
    )
    
    input_value: str = Field(
        ...,
        description="Text to type or key to press (empty string if not needed)"
    )
    
    reasoning: str = Field(
        ...,
        min_length=1,
        description="Explanation of why this action was chosen (cannot be empty)"
    )
    
    outcome_mismatch: str = Field(
        ...,
        description="Description of previous action failure (empty string if none)"
    )
    
    class Config:
        # Strict validation - no extra fields allowed
        extra = "forbid"


class PlanRequest(BaseModel):
    system_prompt: str = Field(..., description="System prompt with persona context")
    user_prompt: str = Field(..., description="User prompt with task/history")
    image_base64: str = Field(..., description="Base64-encoded PNG screenshot")


class PlanResponse(BaseModel):
    action: Optional[dict] = Field(None, description="Parsed and validated action")
    raw_output: str = Field(..., description="Raw model output")
    success: bool = Field(..., description="Whether planning succeeded")
    error: Optional[str] = Field(None, description="Error message if success=False")


class HealthResponse(BaseModel):
    status: str
    gpu_available: bool
    model_loaded: bool
    queue_size: int


# -------------------------
# GLOBAL STATE
# -------------------------
app = FastAPI(
    title="Planner Service",
    description="Planning service using Qwen-3-VL-8B-Instruct with Pydantic validation",
    version="1.0.0"
)

model: Optional[Qwen3VLForConditionalGeneration] = None
processor: Optional[AutoProcessor] = None
inference_queue: Queue = Queue(maxsize=MAX_QUEUE_SIZE)

# -------------------------
# MODEL LOADING
# -------------------------
@app.on_event("startup")
async def load_model():
    """Load model once at startup and start inference worker"""
    global model, processor
    
    logger.info("=" * 60)
    logger.info("Starting Planner Service with Pydantic Validation")
    logger.info("=" * 60)
    
    try:
        logger.info(f"Loading model: {MODEL_NAME}")
        
        model = Qwen3VLForConditionalGeneration.from_pretrained(
            MODEL_NAME,
            torch_dtype=torch.bfloat16,
            attn_implementation="flash_attention_2",
            device_map="auto",
        ).eval()
        logger.info("✓ Model loaded")
        
        processor = AutoProcessor.from_pretrained(MODEL_NAME)
        logger.info("✓ Processor loaded")
        
        if torch.cuda.is_available():
            gpu_name = torch.cuda.get_device_name(0)
            gpu_memory = torch.cuda.get_device_properties(0).total_memory / 1e9
            logger.info(f"✓ GPU: {gpu_name} ({gpu_memory:.1f} GB)")
        else:
            logger.warning("⚠ No GPU detected")
        
        asyncio.create_task(inference_worker())
        logger.info("✓ Inference worker started")
        
        logger.info("=" * 60)
        logger.info("Service ready")
        logger.info("=" * 60)
        
    except Exception as e:
        logger.error(f"Failed to load model: {e}")
        raise


# -------------------------
# INFERENCE WORKER
# -------------------------
async def inference_worker():
    """Process inference requests sequentially"""
    logger.info("Inference worker ready")
    
    while True:
        try:
            request, response_future = await inference_queue.get()
            
            try:
                result = await asyncio.to_thread(run_inference, request)
                response_future.set_result(result)
                logger.debug("✓ Inference completed")
                
            except Exception as e:
                logger.error(f"Inference failed: {e}", exc_info=True)
                response_future.set_exception(e)
            
        except Exception as e:
            logger.error(f"Worker error: {e}", exc_info=True)


def run_inference(request: PlanRequest) -> PlanResponse:
    """Run model inference with Pydantic validation"""
    try:
        # Decode image
        image_bytes = base64.b64decode(request.image_base64)
        image = Image.open(BytesIO(image_bytes)).convert("RGB")
        
        temp_path = "/tmp/planner_input.png"
        image.save(temp_path)
        
        # Enhanced JSON instruction with schema
        json_instruction = f"""
CRITICAL: Respond with ONLY valid JSON matching this EXACT schema:

{ActionSchema.model_json_schema()}

Rules:
1. NO text before or after the JSON
2. NO markdown code blocks
3. ALL fields are required
4. Use empty string "" for unused fields (never null or omit)
5. reasoning field CANNOT be empty
6. action_type must be one of: CLICK, TYPE, SCROLL_UP, SCROLL_DOWN, HOVER, DRAG_AND_DROP, PRESS_KEY, BACK, FORWARD, DONE

Example valid response:
{{"action_type": "CLICK", "target_description": "the blue login button", "input_value": "", "reasoning": "Need to access the login form", "outcome_mismatch": ""}}

Your response must start with {{ and end with }}.
"""
        
        full_user_prompt = f"{request.user_prompt}\n\n{json_instruction}"
        
        # Build messages
        messages = [
            {
                "role": "system",
                "content": request.system_prompt
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "image": temp_path,
                    },
                    {
                        "type": "text",
                        "text": full_user_prompt,
                    },
                ],
            }
        ]
        
        # Process chat template
        text = processor.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )
        
        # Process vision info
        image_inputs, video_inputs = process_vision_info(messages)
        
        # Tokenize
        inputs = processor(
            text=[text],
            images=image_inputs,
            videos=video_inputs,
            padding=True,
            return_tensors="pt"
        )
        inputs = inputs.to(model.device)
        
        # Generate
        with torch.inference_mode():
            generated_ids = model.generate(
                **inputs,
                **GENERATION_CONFIG,
            )
        
        # Decode
        generated_ids_trimmed = [
            out_ids[len(in_ids):]
            for in_ids, out_ids in zip(inputs.input_ids, generated_ids)
        ]
        
        output_text = processor.batch_decode(
            generated_ids_trimmed,
            skip_special_tokens=True,
            clean_up_tokenization_spaces=False
        )[0].strip()
        
        logger.info(f"Model output: {output_text[:200]}")
        
        # Parse and validate JSON with Pydantic
        try:
            # Clean output
            cleaned = output_text
            if "```json" in cleaned:
                cleaned = cleaned.split("```json")[1].split("```")[0].strip()
            elif "```" in cleaned:
                cleaned = cleaned.split("```")[1].split("```")[0].strip()
            
            logger.info(f"Cleaned output: {cleaned[:300]}")
            
            # Parse JSON
            parsed_data = json.loads(cleaned)
            
            # Validate with Pydantic - THIS IS THE KEY STEP
            validated_action = ActionSchema.model_validate(parsed_data)
            
            # Convert validated Pydantic model to dict
            action_dict = validated_action.model_dump()
            
            logger.info(f"✓ Validated action: {action_dict['action_type']}")
            
            return PlanResponse(
                action=action_dict,
                raw_output=output_text,
                success=True,
                error=None
            )
            
        except json.JSONDecodeError as e:
            logger.warning(f"JSON decode error: {e}\nOutput: {output_text[:300]}")
            return PlanResponse(
                action=None,
                raw_output=output_text,
                success=False,
                error=f"Invalid JSON: {str(e)}"
            )
        
        except ValidationError as e:
            # Pydantic validation failed - output doesn't match schema
            logger.warning(f"Schema validation error: {e}\nParsed: {parsed_data}")
            return PlanResponse(
                action=None,
                raw_output=output_text,
                success=False,
                error=f"Schema validation failed: {str(e)}"
            )
        
    except Exception as e:
        logger.error(f"Inference error: {e}", exc_info=True)
        return PlanResponse(
            action=None,
            raw_output="",
            success=False,
            error=str(e)
        )


# -------------------------
# API ENDPOINTS
# -------------------------
@app.post("/plan", response_model=PlanResponse)
async def plan_action(request: PlanRequest):
    """Plan next action based on screenshot and context"""
    
    if model is None:
        raise HTTPException(status_code=503, detail="Model not loaded")
    
    if inference_queue.full():
        raise HTTPException(
            status_code=429,
            detail=f"Queue full ({MAX_QUEUE_SIZE} requests)"
        )
    
    logger.info(f"Planning request received")
    
    response_future = asyncio.Future()
    
    try:
        await inference_queue.put((request, response_future))
    except asyncio.QueueFull:
        raise HTTPException(status_code=429, detail="Queue full")
    
    try:
        result = await asyncio.wait_for(response_future, timeout=INFERENCE_TIMEOUT)
        
        if result.success:
            logger.info(f"✓ Planned: {result.action['action_type']}")
        else:
            logger.warning(f"✗ Planning failed: {result.error}")
        
        return result
        
    except asyncio.TimeoutError:
        logger.error(f"Timeout after {INFERENCE_TIMEOUT}s")
        raise HTTPException(
            status_code=504,
            detail=f"Inference timeout after {INFERENCE_TIMEOUT}s"
        )


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint"""
    return HealthResponse(
        status="healthy" if model is not None else "loading",
        gpu_available=torch.cuda.is_available(),
        model_loaded=model is not None,
        queue_size=inference_queue.qsize()
    )


@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "service": "Planner Service",
        "version": "1.0.0",
        "model": MODEL_NAME,
        "validation": "Pydantic",
        "docs": "/docs",
        "health": "/health"
    }


# -------------------------
# ENTRY POINT
# -------------------------
if __name__ == "__main__":
    import uvicorn
    import os
    
    host = os.getenv("PLANNER_SERVICE_HOST", "127.0.0.1")
    port = int(os.getenv("PLANNER_SERVICE_PORT", "8001"))
    
    logger.info(f"Starting server on {host}:{port}")
    
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info"
    )
