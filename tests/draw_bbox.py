from PIL import Image, ImageDraw

# Load original image
img = Image.open("test_image.png").convert("RGB")
w, h = img.size

# Normalized bbox from your model output
bbox_norm = [0.3257, 0.9600, 0.4988, 1.0000]

# Convert to original image pixels
x1 = int(bbox_norm[0] * w)
y1 = int(bbox_norm[1] * h)
x2 = int(bbox_norm[2] * w)
y2 = int(bbox_norm[3] * h)

draw = ImageDraw.Draw(img)
draw.rectangle([x1, y1, x2, y2], outline="red", width=4)

img.save("test_image_with_bbox.png")
print("Saved test_image_with_bbox.png")
