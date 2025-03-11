# Inkplate 6 Color Firmware Update Guide

Below are step-by-step instructions for pushing firmware updates to Inkplate 6COLOR devices.

## Prerequisites
- Arduino IDE installed
- Access to S3 for uploading firmware files
- Optional: Physical Inkplate 6COLOR device to verify update was successful

## Update Process

### 1. Upload the Current Firmware (Checkpoint Build)
a. Ensure the firmware is in a stable state and ready for compilation.
b. Upload the firmware to the device using the Arduino IDE (**do not change the version number yet**).
c. Keep the device connected and monitor logs via the **Serial Monitor** to confirm successful upload.

### 2. Increment and Compile the Firmware
a. Update the version number in the firmware code.
b. Compile the firmware again using **Verify** in Arduino IDE (**do not upload this version yet**).
c. Ensure the version number is incremented from the currently installed firmware.

### 3. Retrieve the Compiled Firmware
a. Locate the compiled firmware file in (update path based on your own machine):
   ```
   /Users/billy/Library/Caches/arduino/sketches
   ```
b. Find the `inkplate-6color.ino.bin` file.

### 4. Create the Version File
a. Create a new text file named `version.txt`.
b. Inside `version.txt`, enter the updated version number that matches the firmware.

### 5. Upload Files to S3
a. Upload both `inkplate-6color.ino.bin` and `version.txt` to the designated S3 bucket.

### 6. (If Device Available) Trigger the Update on the Device
a. Press the **Wake** button on the Inkplate 6 Color to initiate a new wake-up and update process.
b. Since the e-paper display refreshes approximately every 30 seconds, immediate feedback will not be visible.
c. Use the **Serial Monitor** to verify the update process.
- Expected confirmation logs:
  ```
  Checking for firmware update...
  Found version.txt in S3
  New firmware version detected: x.x.x
  Downloading and installing update...
  Firmware update successful!
  ```

Once these steps are completed successfully, your Inkplate 6 Color should be running the latest firmware.

---

For any troubleshooting or additional details, refer to the official Inkplate documentation or contact support.

