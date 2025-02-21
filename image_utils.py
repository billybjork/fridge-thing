import numpy as np
from PIL import Image

def fill_letterbox(img: Image.Image, target_width: int, target_height: int) -> Image.Image:
    """
    Letterbox the image by adding sidebars filled with the average edge color.
    """
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

    # Combine the arrays: left/right fills then top/bottom fills.
    img_np = np.hstack([left_fill_array, img_np, right_fill_array])
    img_np = np.vstack([top_fill_array, img_np, bottom_fill_array])

    # Convert back to a PIL Image
    return Image.fromarray(img_np.astype('uint8'))