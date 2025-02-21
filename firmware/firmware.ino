#define FS_NO_GLOBALS

#include <WiFi.h>
#include <ESPAsyncWebServer.h>
#include <Preferences.h>
#include <DNSServer.h>
#include <ESPmDNS.h>
#include <HTTPClient.h>
#include <WiFiClientSecure.h>
#include <ArduinoJson.h>
#include <Inkplate.h>
#include <esp_sleep.h>
#include <HTTPUpdate.h>

// Create Inkplate object
Inkplate display;

// Global objects
Preferences preferences;
AsyncWebServer server(80);
DNSServer dnsServer;

// OTA configuration
const char* currentFirmwareVersion = "1.2";  // Current firmware version
const char* versionCheckURL = "https://s3.us-west-1.amazonaws.com/bjork.love/fridge-thing-firmware/version.txt";
const char* firmwareURL = "https://s3.us-west-1.amazonaws.com/bjork.love/fridge-thing-firmware/firmware.ino.bin";

// Access Point settings
const char *apSSID = "FridgeThing";
const char *apPassword = "";

// HTML content for Wi-Fi setup page
const char *htmlSetupPage =
"<!DOCTYPE html>\n"
"<html>\n"
"  <head>\n"
"    <meta charset=\"UTF-8\">\n"
"    <title>Fridge Thing - Wi-Fi Setup</title>\n"
"    <style>\n"
"      body {\n"
"        font-family: Arial, sans-serif;\n"
"        font-size: 20px;\n"
"        background-color: #f8f8f8;\n"
"        text-align: center;\n"
"        margin: 20px;\n"
"      }\n"
"      h2 {\n"
"        font-size: 28px;\n"
"        margin-bottom: 20px;\n"
"      }\n"
"      input[type=\"text\"],\n"
"      input[type=\"password\"] {\n"
"        font-size: 20px;\n"
"        padding: 10px;\n"
"        margin: 10px 0;\n"
"        width: 80%;\n"
"        max-width: 400px;\n"
"      }\n"
"      input[type=\"submit\"] {\n"
"        font-size: 20px;\n"
"        padding: 10px 20px;\n"
"        margin-top: 20px;\n"
"      }\n"
"    </style>\n"
"  </head>\n"
"  <body>\n"
"    <h2>Enter Wi-Fi Details</h2>\n"
"    <form action=\"/setup\" method=\"post\">\n"
"      <input type=\"text\" name=\"ssid\" placeholder=\"Wi-Fi Name\"><br>\n"
"      <input type=\"password\" name=\"password\" placeholder=\"Wi-Fi Password\"><br>\n"
"      <input type=\"submit\" value=\"Save\">\n"
"    </form>\n"
"  </body>\n"
"</html>\n";

// HTML for captive portal redirection
const char *htmlRedirect =
"<!DOCTYPE html>\n"
"<html>\n"
"  <head>\n"
"    <meta http-equiv=\"refresh\" content=\"0; url=http://fridgething.local/\">\n"
"    <title>Redirecting...</title>\n"
"  </head>\n"
"  <body>\n"
"    <p>Redirecting to Wi-Fi Setup...</p>\n"
"  </body>\n"
"</html>\n";

// Timeout for captive portal (5 min = 300,000 ms)
static const unsigned long CAPTIVE_PORTAL_TIMEOUT_MS = 300000;
unsigned long captivePortalStartTime = 0;

// Helper function: Convert voltage (3.2V–4.2V) to approximate battery percentage
float voltageToPercent(float voltage) {
    // Linear approximation for LiPo battery:
    // 4.2V -> 100% and 3.2V -> 0%
    float pct = (voltage - 3.2f) * 100.0f / (4.2f - 3.2f);
    if (pct > 100.0f) pct = 100.0f;
    if (pct < 0.0f)   pct = 0.0f;
    return pct;
}

/**
 * Download a file (BMP) from 'imageUrl' using WiFiClient
 * and store it on the SD card at 'localPath' (e.g. "/temp.bmp")
 * using SdFat's SdFile. Return true if successful, false otherwise.
 */
bool downloadToSD(const String &imageUrl, const String &localPath, WiFiClient &client)
{
    HTTPClient http;
    http.setTimeout(10000);

    Serial.println("Downloading from: " + imageUrl);
    if (!http.begin(client, imageUrl)) {
        Serial.println("ERROR: http.begin() failed");
        return false;
    }

    int httpCode = http.GET();
    if (httpCode != 200) {
        Serial.printf("ERROR: HTTP GET code=%d\n", httpCode);
        http.end();
        return false;
    }

    WiFiClient *stream = http.getStreamPtr();
    if (!stream) {
        Serial.println("ERROR: No stream from HTTP");
        http.end();
        return false;
    }

    SdFile outFile;
    if (!outFile.open(localPath.c_str(), O_WRITE | O_CREAT | O_TRUNC)) {
        Serial.println("ERROR: Could not open file on SD");
        http.end();
        return false;
    }

    uint8_t buff[512];
    int totalBytes = 0;
    unsigned long lastReadTime = millis();
    // Loop until the stream is no longer providing data for 10 seconds
    while ((millis() - lastReadTime) < 10000) {
        size_t availableBytes = stream->available();
        if (availableBytes > 0) {
            lastReadTime = millis();  // reset timeout since we got data
            int bytesRead = stream->readBytes((char*)buff, (availableBytes > sizeof(buff)) ? sizeof(buff) : availableBytes);
            outFile.write(buff, bytesRead);
            totalBytes += bytesRead;
        } else if (!stream->connected()) {
            // If not connected and no data available, assume download complete.
            break;
        }
        delay(1);
    }

    outFile.close();
    http.end();

    Serial.printf("Downloaded %d bytes -> %s\n", totalBytes, localPath.c_str());
    return (totalBytes > 0);
}

/**
 * Fetch a BMP image from the server, store on SD, then display it with drawImage().
 * Additionally, overlay a low battery indicator if battery is below 10%.
 */
void fetchAndDisplayImage() {
    if (WiFi.status() != WL_CONNECTED) {
        Serial.println("ERROR: Not connected to Wi-Fi");
        return;
    }

    // Generate a unique device ID based on the ESP32's MAC address.
    // The ESP32 has a unique 48-bit MAC address which we format as a hexadecimal string.
    uint64_t chipid = ESP.getEfuseMac();
    char deviceId[17]; // 16 characters + null terminator
    sprintf(deviceId, "%04X%08X", (uint16_t)(chipid >> 32), (uint32_t)chipid);
    String deviceUuid = String(deviceId);
    Serial.println("Device UUID: " + deviceUuid);

    // The server route: /api/devices/{device_uuid}/display
    String serverUrl = "https://fridge-thing-production.up.railway.app/api/devices/" + deviceUuid + "/display";
    WiFiClientSecure client;
    client.setInsecure();

    // 1) POST to get {image_url, next_wake_secs}
    HTTPClient http;
    http.setTimeout(10000);
    http.begin(client, serverUrl);
    http.addHeader("Content-Type", "application/json");

    StaticJsonDocument<256> doc;
    doc["current_fw_ver"] = currentFirmwareVersion;
    String body;
    serializeJson(doc, body);

    int httpCode = http.POST(body);
    if (httpCode != 200) {
        Serial.printf("ERROR: POST /api/devices/.../display code=%d\n", httpCode);
        http.end();
        return;
    }

    String resp = http.getString();
    Serial.println("Server response: " + resp);

    StaticJsonDocument<512> respDoc;
    DeserializationError err = deserializeJson(respDoc, resp);
    http.end();
    if (err) {
        Serial.println("ERROR: JSON parse failed");
        return;
    }

    // Extract data from server response
    String imageUrl    = respDoc["image_url"].as<String>();
    long   nextWakeSec = respDoc["next_wake_secs"].as<long>();

    if (imageUrl.startsWith("http://")) {
        imageUrl.replace("http://", "https://");
    }

    // 2) Download file to SD using the secure client (WiFiClientSecure)
    const String localPath = "/temp.bmp";
    if (!downloadToSD(imageUrl, localPath, client)) {
        Serial.println("ERROR: Could not download image");
        return;
    }

    // 3) Render the downloaded image with drawImage()
    Serial.println("Rendering downloaded image with drawImage...");
    bool ok = display.drawImage(localPath.c_str(), 0, 0);
    if (!ok) {
        Serial.println("ERROR: drawImage failed");
    } else {
        Serial.println("BMP image displayed successfully.");
    }

    // --- Low Battery Indicator Implementation ---
    double voltage = display.readBattery();
    float batteryPercent = voltageToPercent(voltage);
    Serial.print("Battery Voltage: ");
    Serial.print(voltage, 2);
    Serial.print(" V (");
    Serial.print(batteryPercent, 1);
    Serial.println("%)");

    if (batteryPercent < 10.0f) {
        // Draw a white rectangle to clear an area and overlay the warning text.
        display.fillRect(0, 0, 200, 30, WHITE); // Adjust the dimensions as needed.
        display.setTextSize(2);
        display.setTextColor(BLACK);
        display.setCursor(10, 10);
        display.print("LOW BATTERY!");
    }
    // --- End Low Battery Indicator ---

    // Update the display with the final content
    display.display();

    // 4) Deep sleep
    Serial.printf("Going to deep sleep for %ld seconds...\n", nextWakeSec);
    // Enable timer wakeup for the next scheduled image update.
    esp_sleep_enable_timer_wakeup(nextWakeSec * 1000000ULL);
    // Enable external wakeup from the Inkplate wake button (GPIO 36).
    esp_sleep_enable_ext0_wakeup(GPIO_NUM_36, 0);
    esp_deep_sleep_start();
}

/**
 * Check for OTA firmware updates.
 *
 * This function makes an HTTPS request to fetch the latest firmware version.
 * If it differs from the current version, it triggers an OTA update using the ESP32's
 * built-in HTTPUpdate library.
 *
 * Note: OTA updates require an active Internet connection.
 */
void checkOTAUpdate() {
    Serial.println("Checking for OTA firmware update...");
    WiFiClientSecure client;
    client.setInsecure();  // For simplicity; for production, set up proper certificate validation.
    
    HTTPClient http;
    http.setTimeout(10000);
    if (http.begin(client, versionCheckURL)) {
        int httpCode = http.GET();
        if (httpCode == 200) {
            String newVersion = http.getString();
            newVersion.trim();
            Serial.println("Latest firmware version available: " + newVersion);
            if (newVersion != String(currentFirmwareVersion)) {
                Serial.println("New firmware version available. Starting OTA update...");
                // Trigger OTA update using the firmware binary URL.
                t_httpUpdate_return ret = httpUpdate.update(client, firmwareURL);
                switch(ret) {
                    case HTTP_UPDATE_FAILED:
                        Serial.printf("HTTP_UPDATE_FAILED Error (%d): %s\n", httpUpdate.getLastError(), httpUpdate.getLastErrorString().c_str());
                        break;
                    case HTTP_UPDATE_NO_UPDATES:
                        Serial.println("No OTA update available (unexpected).");
                        break;
                    case HTTP_UPDATE_OK:
                        // On success, the device will reboot automatically.
                        break;
                }
            } else {
                Serial.println("Firmware is up-to-date.");
            }
        } else {
            Serial.printf("Failed to check version, HTTP GET code: %d\n", httpCode);
        }
        http.end();
    } else {
        Serial.println("Failed to initiate HTTP connection for OTA version check.");
    }
}

/**
 * Setup: Initialize SD, connect Wi-Fi, start captive portal if needed.
 */
void setup() {
    Serial.begin(115200);

    preferences.begin("wifi", false);
    String storedSSID = preferences.getString("ssid", "");
    String storedPass = preferences.getString("password", "");
    preferences.end();

    // Initialize Inkplate display
    display.begin();

    // Initialize SD card
    if (!display.sdCardInit()) {
        Serial.println("SD init failed. We'll keep going, but can't store images!");
    }

    // Only display booting screen on a cold start.
    // If waking from deep sleep (timer or ext0 wakeup), skip booting to allow a seamless image refresh.
    if (esp_sleep_get_wakeup_cause() != ESP_SLEEP_WAKEUP_TIMER &&
        esp_sleep_get_wakeup_cause() != ESP_SLEEP_WAKEUP_EXT0) {
        display.clearDisplay();
        display.setTextColor(BLACK);
        display.setTextSize(4);
        display.setCursor(10, 20);
        display.print("Booting...");
        display.display();
    }

    // Attempt Wi-Fi connection using stored credentials
    if (storedSSID != "") {
        WiFi.mode(WIFI_STA);
        WiFi.begin(storedSSID.c_str(), storedPass.c_str());

        int attempts = 0;
        while (WiFi.status() != WL_CONNECTED && attempts < 15) {
            delay(1000);
            Serial.print(".");
            attempts++;
        }

        if (WiFi.status() == WL_CONNECTED) {
            Serial.println("\nConnected to Wi-Fi!");
            Serial.print("IP: ");
            Serial.println(WiFi.localIP());

            // Check for OTA update immediately after Wi-Fi connects.
            checkOTAUpdate();
            // If no OTA update is triggered (or after update failure), continue with normal operation.
            fetchAndDisplayImage();
            return;
        }
    }

    // If Wi-Fi fails, start the captive portal.
    startCaptivePortal();
}

/**
 * Start captive portal if no Wi-Fi credentials exist or connection fails.
 */
void startCaptivePortal() {
    Serial.println("\nStarting Captive Portal...");
    WiFi.mode(WIFI_AP);
    WiFi.softAP(apSSID, apPassword);

    // Show instructions on display
    display.clearDisplay();
    display.setTextColor(BLACK);
    display.setTextSize(5);
    display.setCursor(10, 10);
    display.print("Wi-Fi Setup");
    display.setTextSize(2);
    display.setCursor(10, 80);
    display.print("Connect to the wi-fi network 'FridgeThing'");
    display.setCursor(10, 120);
    display.print("If not prompted automatically, visit: http://fridgething.local/");
    display.display();

    // Start DNS redirection
    dnsServer.start(53, "*", WiFi.softAPIP());

    // Start mDNS
    if (!MDNS.begin("fridgething")) {
        Serial.println("Error starting mDNS");
    }

    // Setup captive portal routes
    server.on("/", HTTP_GET, [](AsyncWebServerRequest *request) {
        request->send(200, "text/html", htmlSetupPage);
    });

    server.on("/setup", HTTP_POST, [](AsyncWebServerRequest *request) {
        if (request->hasParam("ssid", true) && request->hasParam("password", true)) {
            String newSSID = request->getParam("ssid", true)->value();
            String newPassword = request->getParam("password", true)->value();

            preferences.begin("wifi", false);
            preferences.putString("ssid", newSSID);
            preferences.putString("password", newPassword);
            preferences.end();

            request->send(200, "text/html",
                          "<html><body><h2>Wi-Fi Configured!</h2><p>Restarting...</p></body></html>");
            delay(2000);
            ESP.restart();
        } else {
            request->send(400, "text/plain", "Missing SSID or Password");
        }
    });

    // Common captive portal redirections
    server.on("/generate_204", HTTP_GET, [](AsyncWebServerRequest *request) {
        request->send(200, "text/html", htmlRedirect);
    });
    server.on("/hotspot-detect.html", HTTP_GET, [](AsyncWebServerRequest *request) {
        request->send(200, "text/html", htmlRedirect);
    });
    server.on("/fwlink", HTTP_GET, [](AsyncWebServerRequest *request) {
        request->send(200, "text/html", htmlRedirect);
    });
    server.onNotFound([](AsyncWebServerRequest *request) {
        request->send(200, "text/html", htmlRedirect);
    });

    // Start HTTP server
    server.begin();

    // Record the time we started (for eventual timeout)
    captivePortalStartTime = millis();
}

/**
 * Main loop:
 * - Handle captive portal timeouts.
 */
void loop() {
    dnsServer.processNextRequest();

    // If in AP mode, check if user took too long
    if (WiFi.getMode() == WIFI_AP && captivePortalStartTime > 0) {
        unsigned long elapsed = millis() - captivePortalStartTime;
        if (elapsed >= CAPTIVE_PORTAL_TIMEOUT_MS) {  // 5 minutes
            Serial.println("No Wi-Fi config received; going to deep sleep...");

            // Sleep for 30 seconds (or any appropriate fallback)
            esp_sleep_enable_timer_wakeup(30ULL * 1000000ULL);
            // Enable external wakeup via the Inkplate wake button (GPIO 36)
            esp_sleep_enable_ext0_wakeup(GPIO_NUM_36, 0);
            display.clearDisplay();
            display.setCursor(10, 50);
            display.setTextSize(2);
            display.print("Going to sleep...");
            display.display();
            delay(1000);

            esp_deep_sleep_start();
        }
    }
}