import os
from PIL import Image
import statistics

# Configure paths
input_dir = "/Users/billy/Dropbox/Projects/anythingframe/images/test-set-1"
output_dir = "/Users/billy/Dropbox/Projects/anythingframe/images/_converted"
os.makedirs(output_dir, exist_ok=True)

# Display dimensions
TARGET_WIDTH = 800
TARGET_HEIGHT = 480

# Valid extensions
valid_extensions = {".jpg", ".jpeg", ".png", ".webp"}

for filename in os.listdir(input_dir):
    base, ext = os.path.splitext(filename.lower())
    if ext not in valid_extensions:
        continue

    input_path = os.path.join(input_dir, filename)
    image = Image.open(input_path).convert("RGB")
    w, h = image.size

    # Rotate if vertical
    if h > w:
        image = image.rotate(90, expand=True)
        w, h = image.size

    # Compute scaling factor to fit within TARGET_WIDTH x TARGET_HEIGHT
    scale = min(TARGET_WIDTH / w, TARGET_HEIGHT / h)
    new_w = int(w * scale)
    new_h = int(h * scale)

    # Resize the image
    image = image.resize((new_w, new_h), Image.Resampling.LANCZOS)

    # If image already fits exactly, just save and continue
    if new_w == TARGET_WIDTH and new_h == TARGET_HEIGHT:
        image.save(os.path.join(output_dir, f"{base}_processed.png"))
        continue

    # Otherwise, we need to create a background with adaptive color
    # We'll sample the pixels on the border of the resized image and average them
    pixels = image.load()

    # Collect border pixels (top, bottom, left, right edges)
    border_colors = []
    # Top and bottom rows
    for x in range(new_w):
        border_colors.append(pixels[x, 0])
        border_colors.append(pixels[x, new_h - 1])
    # Left and right columns
    for y in range(new_h):
        border_colors.append(pixels[0, y])
        border_colors.append(pixels[new_w - 1, y])

    # Average the border colors
    # Each pixel is an (R,G,B) tuple. We'll average each channel separately.
    reds = [c[0] for c in border_colors]
    greens = [c[1] for c in border_colors]
    blues = [c[2] for c in border_colors]

    avg_color = (
        int(statistics.mean(reds)),
        int(statistics.mean(greens)),
        int(statistics.mean(blues))
    )

    # Create a new image with the background color
    background = Image.new("RGB", (TARGET_WIDTH, TARGET_HEIGHT), avg_color)

    # Compute position to center the resized image on the background
    offset_x = (TARGET_WIDTH - new_w) // 2
    offset_y = (TARGET_HEIGHT - new_h) // 2

    # Paste the resized image onto the background
    background.paste(image, (offset_x, offset_y))

    # Save the final image
    output_path = os.path.join(output_dir, f"{base}_processed.png")
    background.save(output_path)
