from PIL import Image
import os

# Folder containing .jpg images
source_dir = "C:/xampp/htdocs/miniplexindia/static/images"

# Convert all .jpg to .webp
for filename in os.listdir(source_dir):
    if filename.lower().endswith(".jpg"):
        full_path = os.path.join(source_dir, filename)
        webp_path = os.path.join(source_dir, filename.replace(".jpg", ".webp"))
        img = Image.open(full_path)
        img.save(webp_path, "webp", quality=10)
        print("Converted:", filename, "->", os.path.basename(webp_path))
