#include "Inkplate.h"
#include <SdFat.h>  // The SD library typically used by Inkplate

// Create an Inkplate object.
Inkplate display;

// Directory where images are stored on SD
const char* IMAGE_DIR = "/images";

// SD file objects for iteration
SdFile root;
SdFile currentFile;

// Delay between images (in minutes)
const int DELAY_MINUTES = 1;

// Threshold for “low battery” warning (%)
const float BATTERY_WARN_PERCENT = 5.0;

// Helper: Convert voltage (3.2 V–4.2 V) to approximate battery percentage
float voltageToPercent(float voltage) {
    // Basic linear approximation for LiPo:
    // 4.2 V  ~ 100%
    // 3.2 V  ~ 0%
    float pct = (voltage - 3.2f) * 100.0f / (4.2f - 3.2f);
    if (pct > 100.0f) pct = 100.0f;
    if (pct < 0.0f)   pct = 0.0f;
    return pct;
}

void setup() {
    Serial.begin(115200);

    // Initialize the Inkplate display
    display.begin();

    // Initialize the SD card
    if (!display.sdCardInit()) {
        Serial.println("SD card initialization failed!");
        while (1) { delay(100); }
    }
    Serial.println("SD card initialized.");

    // Open the root directory
    if (!root.open(IMAGE_DIR, O_RDONLY)) {
        Serial.println("Could not open image directory.");
        while (1) { delay(100); }
    }
}

void loop() {
    // --- 1) Check the battery voltage and show a low-battery warning if needed ---
    double voltage = display.readBattery();
    float pct      = voltageToPercent(voltage);

    Serial.print("Battery Voltage: ");
    Serial.print(voltage, 2);
    Serial.print(" V (");
    Serial.print(pct, 1);
    Serial.println("%)");

    bool isLowBattery = (pct <= BATTERY_WARN_PERCENT);

    // --- 2) Grab the next BMP file from the directory ---
    if (!currentFile.openNext(&root, O_RDONLY)) {
        // If we've reached the end of the directory, rewind and re-open
        Serial.println("End of directory reached. Rewinding...");
        root.rewind();
        root.close();
        if (!root.open(IMAGE_DIR, O_RDONLY)) {
            Serial.println("Could not re-open image directory.");
            delay(DELAY_MINUTES * 60 * 1000UL);
            return;
        }
        return; 
    }

    // --- 3) Extract the file name, check extension ---
    char fileName[64];
    if (!currentFile.getName(fileName, sizeof(fileName))) {
        Serial.println("Failed to get file name.");
        currentFile.close();
        return;
    }

    String fileNameStr = String(fileName);
    currentFile.close(); // done reading name
    Serial.print("Found file: ");
    Serial.println(fileNameStr);

    // --- 4) Draw the new image, and optionally overlay the battery warning ---
    if (fileNameStr.endsWith(".bmp") || fileNameStr.endsWith(".BMP")) {
        bool success = display.drawImage(String(IMAGE_DIR) + "/" + fileNameStr, 0, 0);
        if (success) {
            Serial.println("Image displayed successfully.");
        } else {
            Serial.println("Failed to display BMP image.");
        }
    } else {
        Serial.println("Skipping non-BMP file.");
    }

    if (isLowBattery) {
        // Clear only the area for the battery warning
        display.fillRect(0, 0, 200, 30, WHITE); // Adjust coordinates and size as needed
        display.setTextSize(2);
        display.setTextColor(BLACK);
        display.setCursor(10, 10);
        display.print("LOW BATTERY!");
    }

    // Update the actual e-ink display with the final content
    display.display();

    // --- 5) Wait the desired time before showing the next image ---
    delay(DELAY_MINUTES * 60 * 1000UL);
}
