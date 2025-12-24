import os
import json
import io
import re
from datetime import datetime, timedelta
from PIL import Image, ImageTk
from PIL.ExifTags import TAGS, GPSTAGS
import tkinter as tk
from tkinter import simpledialog

# Configuration
INPUT_HTML = "index_ref.html"
OUTPUT_HTML = "index.html"
PHOTOS_DIR = "photos"

try:
    from google.cloud import vision
    VISION_API_AVAILABLE = True
except ImportError:
    VISION_API_AVAILABLE = False
    print("Warning: 'google-cloud-vision' not found. Automatic location detection will be disabled.")

def get_exif_data(image):
    """Returns a dictionary from the exif data of an PIL Image item. Also converts the GPS Tags"""
    exif_data = {}
    info = image._getexif()
    if info:
        for tag, value in info.items():
            decoded = TAGS.get(tag, tag)
            if decoded == "GPSInfo":
                gps_data = {}
                for t in value:
                    sub_decoded = GPSTAGS.get(t, t)
                    gps_data[sub_decoded] = value[t]
                exif_data[decoded] = gps_data
            else:
                exif_data[decoded] = value
    return exif_data

def get_decimal_from_dms(dms, ref):
    """Helper to convert degrees/minutes/seconds to decimal degrees."""
    degrees = dms[0]
    minutes = dms[1]
    seconds = dms[2]

    decimal = degrees + (minutes / 60.0) + (seconds / 3600.0)
    
    if ref in ['S', 'W']:
        decimal = -decimal
        
    return decimal

def get_lat_lon(exif_data):
    """Returns the latitude and longitude, if available, from the provided exif_data (obtained through get_exif_data above)"""
    lat = None
    lon = None

    if "GPSInfo" in exif_data:
        gps_info = exif_data["GPSInfo"]
        
        gps_latitude = gps_info.get("GPSLatitude")
        gps_latitude_ref = gps_info.get("GPSLatitudeRef")
        gps_longitude = gps_info.get("GPSLongitude")
        gps_longitude_ref = gps_info.get("GPSLongitudeRef")

        if gps_latitude and gps_latitude_ref and gps_longitude and gps_longitude_ref:
            lat = get_decimal_from_dms(gps_latitude, gps_latitude_ref)
            lon = get_decimal_from_dms(gps_longitude, gps_longitude_ref)

    return lat, lon

def get_manual_gps(filepath, root):
    """Opens a window showing the image and asking for GPS coordinates."""
    filename = os.path.basename(filepath)
    
    window = tk.Toplevel(root)
    window.title(f"Enter GPS for {filename}")
    
    def on_close():
        window.destroy()
    window.protocol("WM_DELETE_WINDOW", on_close)
    
    # Load and display image
    try:
        img = Image.open(filepath)
        img.thumbnail((800, 600))  # Resize to fit screen
        tk_img = ImageTk.PhotoImage(img)
        lbl = tk.Label(window, image=tk_img)
        lbl.image = tk_img  # Keep reference
        lbl.pack(padx=10, pady=10)
    except Exception as e:
        tk.Label(window, text=f"Could not load image: {e}").pack(padx=10, pady=10)

    # Input fields
    frame = tk.Frame(window)
    frame.pack(padx=10, pady=10)

    tk.Label(frame, text="Latitude:").grid(row=0, column=0, padx=5)
    lat_entry = tk.Entry(frame)
    lat_entry.grid(row=0, column=1, padx=5)

    tk.Label(frame, text="Longitude:").grid(row=1, column=0, padx=5)
    lon_entry = tk.Entry(frame)
    lon_entry.grid(row=1, column=1, padx=5)

    result = {"lat": None, "lon": None, "stop": False}

    def submit():
        try:
            lat_val = float(lat_entry.get())
            lon_val = float(lon_entry.get())
            if -90 <= lat_val <= 90 and -180 <= lon_val <= 180:
                result["lat"] = lat_val
                result["lon"] = lon_val
                window.destroy()
            else:
                print("Invalid coordinates range.")
        except ValueError:
            print("Invalid number format.")

    def stop_program():
        result["stop"] = True
        window.destroy()

    btn_frame = tk.Frame(window)
    btn_frame.pack(pady=10)

    submit_btn = tk.Button(btn_frame, text="Save Coordinates", command=submit, bg="#dddddd")
    submit_btn.pack(side=tk.LEFT, padx=5)
    
    stop_btn = tk.Button(btn_frame, text="Stop Program", command=stop_program, bg="#ffcccc")
    stop_btn.pack(side=tk.LEFT, padx=5)
    
    window.bind('<Return>', lambda e: submit())
    
    root.wait_window(window)
    
    return result["lat"], result["lon"], result["stop"]

def get_vision_api_location(filepath):
    """Detects landmarks in the file using Google Cloud Vision API."""
    if not VISION_API_AVAILABLE:
        return None, None

    try:
        client = vision.ImageAnnotatorClient()

        with io.open(filepath, 'rb') as image_file:
            content = image_file.read()

        image = vision.Image(content=content)
        response = client.landmark_detection(image=image)
        landmarks = response.landmark_annotations

        if landmarks:
            # Return the first location found
            for landmark in landmarks:
                for location in landmark.locations:
                    lat_lng = location.lat_lng
                    return lat_lng.latitude, lat_lng.longitude
    except Exception as e:
        print(f"Vision API error for {os.path.basename(filepath)}: {e}")
    
    return None, None

def set_gps_location(filepath, lat, lon):
    """Updates the image file with GPS coordinates using piexif. Converts PNG to JPEG if necessary."""
    try:
        import piexif
    except ImportError as e:
        print("Warning: 'piexif' library not found. Cannot update image file. Install it via 'pip install piexif'.")
        raise(e)
        return filepath

    def to_deg(value, loc):
        if value < 0:
            loc_value = loc[1]
        else:
            loc_value = loc[0]
        abs_value = abs(value)
        deg = int(abs_value)
        t1 = (abs_value - deg) * 60
        min = int(t1)
        sec = round((t1 - min) * 60 * 10000)
        return ((deg, 1), (min, 1), (sec, 10000)), loc_value

    original_filepath = filepath

    # Check file extension as piexif only supports JPEG and TIFF
    if not filepath.lower().endswith(('.jpg', '.jpeg', '.tif', '.tiff')):
        if filepath.lower().endswith('.png'):
            try:
                print(f"Converting {os.path.basename(filepath)} to JPEG...")
                with Image.open(filepath) as img:
                    rgb_im = img.convert('RGB')
                    new_filepath = os.path.splitext(filepath)[0] + ".jpg"
                    rgb_im.save(new_filepath, "JPEG", quality=95)
                filepath = new_filepath
            except Exception as e:
                print(f"Error converting {os.path.basename(filepath)}: {e}")
                return original_filepath
        else:
            print(f"Warning: Cannot save GPS to {os.path.basename(filepath)} (Format not supported by piexif).")
            return original_filepath

    try:
        exif_dict = piexif.load(filepath)
        lat_deg, lat_ref = to_deg(lat, ["N", "S"])
        lon_deg, lon_ref = to_deg(lon, ["E", "W"])
        
        exif_dict["GPS"][piexif.GPSIFD.GPSLatitudeRef] = lat_ref
        exif_dict["GPS"][piexif.GPSIFD.GPSLatitude] = lat_deg
        exif_dict["GPS"][piexif.GPSIFD.GPSLongitudeRef] = lon_ref
        exif_dict["GPS"][piexif.GPSIFD.GPSLongitude] = lon_deg
        
        exif_bytes = piexif.dump(exif_dict)
        piexif.insert(exif_bytes, filepath)
        print(f"Saved GPS to {filepath}")
        
        # If converted, remove original
        if filepath != original_filepath and os.path.exists(original_filepath):
            try:
                os.remove(original_filepath)
                print(f"Removed original file: {os.path.basename(original_filepath)}")
            except OSError:
                pass
        
        return filepath
    except Exception as e:
        print(f"Error saving GPS to {filepath}: {e}")
        return original_filepath

def extract_timestamp(filename):
    """Extracts timestamp from filename with format ...2022MMDD_HHMMSS..."""
    # Regex to find 2022MMDD_HHMMSS followed by a separator (~, (, _, space, .) or end of string
    match = re.search(r"2022(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})", filename)
    # match = re.search(r"2022(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})(?=[~(_ .]|$)", filename)
    if match:
        try:
            month, day, hour, minute, second = map(int, match.groups())
            dt = datetime(2022, month, day, hour, minute, second)
            
            if filename.startswith("SAMSUNG"):
                dt -= timedelta(hours=9)
            return dt
        except ValueError as e:
            print(f"Error extracting timestamp from {filename}: {e}")
            return None
    print(f"No timestamp found in {filename} because match={match}")
    return None

def get_reference_images(directory):
    """Scans a directory for images and extracts their timestamps."""
    references = []
    if not os.path.exists(directory):
        print(f"Warning: Reference directory '{directory}' not found.")
        return references
    
    print(f"Indexing images in {directory}...")
    for filename in os.listdir(directory):
        if filename.lower().endswith(('.png', '.jpg', '.jpeg', '.tiff', '.bmp', '.gif')):
            dt = extract_timestamp(filename)
            if dt:
                references.append({
                    "time": dt,
                    "path": os.path.join(directory, filename),
                    "filename": filename
                })
    return references

def find_closest_gps_in_reference(target_filename, reference_images):
    """Finds the GPS coordinates from the closest time-matched reference image."""
    target_time = extract_timestamp(target_filename)
    if not target_time:
        print(f"Warning: Could not extract timestamp from {target_filename}")
        return None, None

    # Calculate delta for all images
    candidates = []
    for ref in reference_images:
        delta = abs(ref["time"] - target_time)
        candidates.append((delta, ref))
    
    if not candidates:
        print("No candidates found.")
        return None, None

    # Sort by time difference (closest first)
    candidates.sort(key=lambda x: x[0])

    # Check for GPS in candidates, starting from the closest
    for delta, ref in candidates:
        try:
            image = Image.open(ref["path"])
            exif = get_exif_data(image)
            lat, lon = get_lat_lon(exif)
            image.close()
            
            if lat and lon:
                print(f"  Found closest match with GPS: {ref['filename']} (Delta: {delta})")
                return lat, lon
        except Exception as e:
            print(f"Error processing {ref['filename']}: {e}")
            continue
    print("Didn't find canditates, return None")
    return None, None

def main():
    print(f"Scanning directory: {PHOTOS_DIR}...")
    
    # Initialize Tkinter root once
    root = tk.Tk()
    root.withdraw()

    # Setup reference directory (all_pictures)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    parent_dir = os.path.dirname(script_dir)
    all_pictures_dir = os.path.join(parent_dir, "photo_all")
    
    reference_images = get_reference_images(all_pictures_dir)

    new_photos_data = []

    # 1. Loop through files in the photos directory
    if not os.path.exists(PHOTOS_DIR):
        print(f"Error: Directory '{PHOTOS_DIR}' not found.")
        return

    for filename in os.listdir(PHOTOS_DIR):
        if filename.lower().endswith(('.png', '.jpg', '.jpeg', '.tiff', '.bmp', '.gif')):
            filepath = os.path.join(PHOTOS_DIR, filename)
            
            try:
                # print(f"Processing {filename}...")
                
                # For every photo, find the closest time match in 'photo_all' and get its GPS.
                lat, lon = find_closest_gps_in_reference(filename, reference_images)

                # if "SAMSUNG_20221127_232618" in filename:
                #     lat = 37.2591425
                #     lon = -112.9510018
                # if "SAMSUNG_20221127_212834" in filename:
                #     lat = 37.2591425
                #     lon = -112.9510018
                # if "SAMSUNG_20221217_112706" in filename:
                #     lat = 40.7021846
                #     lon = -74.0164093

                # for i in ["SAMSUNG_20221123_183528"]:
                #     if i in filename:
                #         print(filename)
                        # print('20221123_223232' in filename)
                        # Fallback to manual input if no reference found
                        # lat, lon, stop = get_manual_gps(filepath, root)
                        # if stop:
                        #     break

                if lat and lon:
                    # Rewrite the GPS data to the file in 'photos'
                    final_path = set_gps_location(filepath, lat, lon)
                    final_filename = os.path.basename(final_path)
                    
                    # Add to map data
                    # Path needs to be relative to the HTML file for the browser
                    web_path = f"{PHOTOS_DIR}/{final_filename}"
                    
                    photo_data = {
                        "lat": lat,
                        "lon": lon,
                        "img": web_path,
                        "title": final_filename
                    }
                    
                    # Check for screenshot (robust check for different extensions or case)
                    screenshot_dir = "screenshot"
                    if os.path.exists(screenshot_dir):
                        target_base = os.path.splitext(final_filename)[0].lower()
                        for f in os.listdir(screenshot_dir):
                            if f.startswith("."): continue
                            if os.path.splitext(f)[0].lower() == target_base:
                                photo_data["screenshot"] = f"{screenshot_dir}/{f}"
                                break
                    
                    new_photos_data.append(photo_data)
                else:
                    # print(f'{lat = }')
                    # print(f'{lon = }')
                    print(f"  Skipping {filename}: No corresponding GPS data found in reference library.")

            except Exception as e:
                print(f"Error processing {filename}: {e}")

    root.destroy()

    # 2. Read the original HTML file
    with open(INPUT_HTML, "r", encoding="utf-8") as f:
        html_content = f.read()

    # 3. Inject the new data into the HTML
    # We convert the python list to a JSON string
    js_data = json.dumps(new_photos_data, indent=4)
    
    # We look for the marker in the HTML to replace the existing array
    # Note: This is a simple string replacement. 
    # It relies on your index.html having 'var photos = ['
    
    start_marker = "var photos = ["
    end_marker = "];"
    
    start_index = html_content.find(start_marker)
    
    if start_index != -1:
        # Find the end of the array definition
        # We start searching for '];' AFTER the start_marker
        end_index = html_content.find(end_marker, start_index)
        
        if end_index != -1:
            # Construct the new HTML
            new_html = (
                html_content[:start_index] + 
                f"var photos = {js_data}" + 
                html_content[end_index + 1:] # +1 to include the semicolon if you want, strictly replacing '];' -> ']'
            )
            
            # Since json.dumps doesn't include the trailing semicolon, we fix the splice:
            # We want: var photos = [ ...json... ];
            
            new_html = (
                html_content[:start_index] + 
                f"var photos = {js_data};" + 
                html_content[end_index + len(end_marker):]
            )

            # 4. Write the output file
            with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
                f.write(new_html)
            
            print(f"Success! Created {OUTPUT_HTML} with {len(new_photos_data)} locations.")
        else:
            print("Error: Could not find the closing '];' for the photos array in HTML.")
    else:
        print("Error: Could not find 'var photos = [' in HTML.")

if __name__ == "__main__":
    main()