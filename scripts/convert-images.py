import os
from PIL import Image, ImageOps
import numpy as np

# Specify the input directory containing images and the output directory for processed images
input_dir = "/Users/billy/Dropbox/Projects/inkplate-6color/src/images/set-3"
output_dir = "/Users/billy/Dropbox/Projects/inkplate-6color/src/images/set-3-converted"
target_resolution = (600, 448)

# Ensure the output directory exists
os.makedirs(output_dir, exist_ok=True)

def fill_letterbox(img, target_width, target_height):
    # Get the current width and height of the image
    current_width, current_height = img.size

    # Calculate letterbox dimensions
    left_fill = (target_width - current_width) // 2
    right_fill = target_width - current_width - left_fill
    top_fill = (target_height - current_height) // 2
    bottom_fill = target_height - current_height - top_fill

    # Convert image to numpy array for processing
    img_np = np.array(img)

    # Calculate average colors for edges
    left_color = img_np[:, 0].mean(axis=0).astype(int)
    right_color = img_np[:, -1].mean(axis=0).astype(int)
    top_color = img_np[0, :].mean(axis=0).astype(int)
    bottom_color = img_np[-1, :].mean(axis=0).astype(int)

    # Create filled regions with average colors
    left_fill_array = np.tile(left_color, (current_height, left_fill, 1))
    right_fill_array = np.tile(right_color, (current_height, right_fill, 1))
    top_fill_array = np.tile(top_color, (top_fill, target_width, 1))
    bottom_fill_array = np.tile(bottom_color, (bottom_fill, target_width, 1))

    # Combine the arrays
    img_np = np.hstack([left_fill_array, img_np, right_fill_array])
    img_np = np.vstack([top_fill_array, img_np, bottom_fill_array])

    # Convert back to PIL Image
    return Image.fromarray(img_np.astype('uint8'))

def process_images():
    for filename in os.listdir(input_dir):
        if filename.lower().endswith((".jpg", ".jpeg", ".JPEG", ".webp", ".png", ".avif")):
            # Open the image
            img_path = os.path.join(input_dir, filename)
            img = Image.open(img_path).convert("RGB")

            # Check orientation and rotate if vertical
            if img.height > img.width:
                img = img.rotate(90, expand=True)

            # Resize the image while maintaining aspect ratio
            img = ImageOps.contain(img, target_resolution)

            # Handle letterboxing
            if img.size != target_resolution:
                img = fill_letterbox(img, *target_resolution)

            # Save the image as BMP
            output_path = os.path.join(output_dir, os.path.splitext(filename)[0] + ".bmp")
            img.save(output_path, format="BMP")
            print(f"Processed and saved: {output_path}")

if __name__ == "__main__":
    process_images()
