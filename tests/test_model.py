import os
import torch
from transformers import (
    Qwen2_5_VLForConditionalGeneration,
    AutoTokenizer,
    AutoProcessor,
)
from qwen_vl_utils import process_vision_info


# -------------------------
# CONFIG
# -------------------------
MODEL_NAME = "inclusionAI/UI-Venus-Ground-7B"
IMAGE_PATH = "/media/disk/roman/test_image.png"
INSTRUCTION = "the blue button with 'Odmítnout vše' text on it"


GENERATION_CONFIG = {
    "max_new_tokens": 64,
    "do_sample": False,
    "temperature": None,
    "top_p": None,
    "top_k": None,
}


# -------------------------
# LOAD MODEL + PROCESSORS
# -------------------------
print("Loading model...")
model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
    MODEL_NAME,
    device_map="auto",
    torch_dtype=torch.bfloat16,
    attn_implementation="flash_attention_2",
).eval()

print("Loading tokenizer...")
tokenizer = AutoTokenizer.from_pretrained(
    MODEL_NAME,
    trust_remote_code=True,
)

print("Loading processor...")
processor = AutoProcessor.from_pretrained(
        MODEL_NAME,
        use_fast=False,
)


# -------------------------
# INFERENCE FUNCTION

def inference(instruction: str, image_path: str):
    assert os.path.isfile(image_path), "Invalid input image path."

    prompt_origin = (
        "Outline the position corresponding to the instruction: {}. "
        "The output should be only [x1,y1,x2,y2]."
    )
    full_prompt = prompt_origin.format(instruction)

    min_pixels = 1_500_000
    max_pixels = 2_400_000

    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "image": image_path,
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
    
    print("Starting generation...")
    with torch.inference_mode():
        generated_ids = model.generate(
            **model_inputs,
            **GENERATION_CONFIG,
        )
    print("Generation finished.")

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

    # -------------------------
    # NORMALIZE COORDINATES
    # -------------------------
    try:
        box = eval(output_text[0])
        input_height = model_inputs["image_grid_thw"][0][1] * 14
        input_width = model_inputs["image_grid_thw"][0][2] * 14

        abs_x1 = float(box[0]) / input_width
        abs_y1 = float(box[1]) / input_height
        abs_x2 = float(box[2]) / input_width
        abs_y2 = float(box[3]) / input_height

        bbox = [abs_x1, abs_y1, abs_x2, abs_y2]
    except Exception:
        bbox = [0.0, 0.0, 0.0, 0.0]

    point = [
        (bbox[0] + bbox[2]) / 2,
        (bbox[1] + bbox[3]) / 2,
    ]

    return {
        "bbox": bbox,
        "point": point,
    }


# -------------------------
# MAIN
# -------------------------
if __name__ == "__main__":
    print("Running inference...")
    result = inference(INSTRUCTION, IMAGE_PATH)

    print("\n=== RESULT ===")
    for k, v in result.items():
        print(f"{k}: {v}")
