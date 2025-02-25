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
const char* currentFirmwareVersion = "1.3";  // Current firmware version
const char* versionCheckURL = "https://s3.us-west-1.amazonaws.com/bjork.love/fridge-thing-firmware/version.txt";
const char* firmwareURL = "https://s3.us-west-1.amazonaws.com/bjork.love/fridge-thing-firmware/firmware.ino.bin";

// Access Point settings
const char *apSSID = "FridgeThing";
const char *apPassword = "";

// State tracking and error handling constants
#define STATE_INITIALIZING     0
#define STATE_CAPTIVE_PORTAL   1
#define STATE_CONNECTING_WIFI  2
#define STATE_CHECKING_UPDATE  3
#define STATE_UPDATING_FW      4
#define STATE_FETCHING_IMAGE   5
#define STATE_DISPLAYING_IMAGE 6
#define STATE_ERROR            7
#define STATE_SLEEPING         8

// Error codes
#define ERROR_NONE             0
#define ERROR_WIFI_CONNECT     1
#define ERROR_SERVER_CONNECT   2
#define ERROR_IMAGE_DOWNLOAD   3
#define ERROR_SD_CARD          4
#define ERROR_OTA_UPDATE       5
#define ERROR_LOW_BATTERY      6

// Global state variables
int currentState = STATE_INITIALIZING;
int errorCode = ERROR_NONE;
uint8_t wifiReconnectAttempts = 0;
bool sdCardAvailable = false;
unsigned long stateChangeTime = 0;
unsigned long lastWifiCheckTime = 0;

// Connection timeouts and intervals
#define WIFI_CONNECT_TIMEOUT   30000     // 30 seconds
#define WIFI_CHECK_INTERVAL    60000     // 1 minute

// Battery thresholds
#define BATTERY_LOW_PCT        10.0f     // 10% battery is low
#define BATTERY_CRITICAL_PCT   5.0f      // 5% battery is critical

// HTML content for Wi-Fi setup page
const char *htmlSetupPage =
"<!DOCTYPE html>\n"
"<html>\n"
"  <head>\n"
"    <meta charset=\"UTF-8\">\n"
"    <title>Fridge Thing - Wi-Fi Setup</title>\n"
"    <style>\n"
"      body { font-family: Arial, sans-serif; font-size: 20px; background-color: #f8f8f8; text-align: center; margin: 20px; }\n"
"      h2 { font-size: 28px; margin-bottom: 20px; }\n"
"      input[type=\"text\"], input[type=\"password\"] { font-size: 20px; padding: 10px; margin: 10px 0; width: 80%; max-width: 400px; }\n"
"      input[type=\"submit\"] { font-size: 20px; padding: 10px 20px; margin-top: 20px; }\n"
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

/**
 * Helper function: Convert voltage (3.2V–4.2V) to approximate battery percentage.
 */
float voltageToPercent(float voltage) {
    float pct = (voltage - 3.2f) * 100.0f / (4.2f - 3.2f);
    if (pct > 100.0f) pct = 100.0f;
    if (pct < 0.0f)   pct = 0.0f;
    return pct;
}

/**
 * Log an event to "log.txt" on the SD card with a timestamp.
 * Here, we use millis() as a simple timestamp.
 */
void logEvent(const char* message) {
    if (!sdCardAvailable) {
        Serial.println("SD card not available for logging.");
        return;
    }
    SdFile logFile;
    // Open log file in append mode.
    if (!logFile.open("/log.txt", O_WRITE | O_CREAT | O_APPEND)) {
        Serial.println("ERROR: Could not open log file.");
        return;
    }
    String logLine = String(millis()) + ": " + message + "\n";
    logFile.write((const uint8_t*)logLine.c_str(), logLine.length());
    logFile.close();
}

/**
 * Update the display to show the current state message.
 * The overlay is shown only in transitional, error, or low-battery states.
 * When an image is successfully rendered, this function does nothing.
 */
void updateStateDisplay(bool fullRefresh = true) {
    // Do not update the overlay when the image is fully displayed with no errors.
    if (currentState == STATE_DISPLAYING_IMAGE && errorCode == ERROR_NONE) {
        return;
    }
    
    if (fullRefresh) {
        display.clearDisplay();
    }
    display.setTextColor(BLACK);
    display.setTextSize(2);
    display.setCursor(10, 10);
    switch (currentState) {
        case STATE_INITIALIZING:
            display.print("Initializing...");
            break;
        case STATE_CAPTIVE_PORTAL:
            display.print("Wi-Fi Setup Mode");
            break;
        case STATE_CONNECTING_WIFI:
            display.print("Connecting Wi-Fi...");
            break;
        case STATE_CHECKING_UPDATE:
            display.print("Checking updates...");
            break;
        case STATE_UPDATING_FW:
            display.print("Updating firmware...");
            break;
        case STATE_FETCHING_IMAGE:
            display.print("Fetching image...");
            break;
        case STATE_ERROR:
            display.print("ERROR: ");
            switch (errorCode) {
                case ERROR_WIFI_CONNECT:
                    display.print("Wi-Fi failed");
                    break;
                case ERROR_SERVER_CONNECT:
                    display.print("Server error");
                    break;
                case ERROR_IMAGE_DOWNLOAD:
                    display.print("Img download fail");
                    break;
                case ERROR_SD_CARD:
                    display.print("SD card error");
                    break;
                case ERROR_OTA_UPDATE:
                    display.print("OTA update fail");
                    break;
                case ERROR_LOW_BATTERY:
                    display.print("Battery critical");
                    break;
                default:
                    display.print("Unknown error");
            }
            break;
        default:
            display.print("State " + String(currentState));
    }
    display.display();
}

/**
 * Set the current state and error code, store them persistently, update display (if needed), and log the event.
 */
void setState(int newState, int newErrorCode = ERROR_NONE) {
    preferences.begin("state", false);
    preferences.putInt("lastState", currentState);
    preferences.putInt("errorCode", newErrorCode);
    preferences.end();
    
    currentState = newState;
    errorCode = newErrorCode;
    stateChangeTime = millis();
    
    String stateMsg = "State changed to: " + String(currentState);
    if (newErrorCode != ERROR_NONE) {
        stateMsg += " (Error: " + String(newErrorCode) + ")";
    }
    Serial.println(stateMsg);
    logEvent(stateMsg.c_str());
    
    // Update overlay only for states that require user feedback.
    updateStateDisplay();
}

/**
 * Check Wi-Fi connection and attempt to reconnect if necessary.
 * Returns true if connected, false otherwise.
 */
bool checkAndReconnectWifi() {
    if (WiFi.status() == WL_CONNECTED) {
        wifiReconnectAttempts = 0;
        return true;
    }
    if (millis() - lastWifiCheckTime < WIFI_CHECK_INTERVAL) {
        return false;
    }
    lastWifiCheckTime = millis();
    
    Serial.println("Wi-Fi disconnected; attempting reconnect...");
    logEvent("Wi-Fi disconnected; attempting reconnect");
    
    preferences.begin("wifi", false);
    String ssid = preferences.getString("ssid", "");
    String password = preferences.getString("password", "");
    preferences.end();
    
    if (ssid.length() == 0) {
        Serial.println("No stored Wi-Fi credentials.");
        logEvent("No stored Wi-Fi credentials.");
        return false;
    }
    
    setState(STATE_CONNECTING_WIFI);
    WiFi.disconnect();
    WiFi.mode(WIFI_STA);
    WiFi.begin(ssid.c_str(), password.c_str());
    
    unsigned long startAttempt = millis();
    while (WiFi.status() != WL_CONNECTED) {
        if (millis() - startAttempt > WIFI_CONNECT_TIMEOUT) {
            wifiReconnectAttempts++;
            Serial.println("Wi-Fi reconnect attempt failed.");
            logEvent("Wi-Fi reconnect attempt failed.");
            if (wifiReconnectAttempts >= 3) {
                Serial.println("Multiple failures; switching to captive portal.");
                logEvent("Multiple Wi-Fi failures; switching to captive portal.");
                return false;
            }
            return false;
        }
        delay(500);
    }
    
    Serial.println("Wi-Fi reconnected successfully!");
    logEvent("Wi-Fi reconnected successfully.");
    wifiReconnectAttempts = 0;
    return true;
}

/**
 * Download a BMP image from 'imageUrl' and store it on the SD card at 'localPath'.
 * Returns true if successful.
 */
bool downloadToSD(const String &imageUrl, const String &localPath, WiFiClient &client) {
    if (!sdCardAvailable) {
        Serial.println("ERROR: SD card not available");
        logEvent("ERROR: SD card not available");
        return false;
    }
    
    HTTPClient http;
    http.setTimeout(10000);
    Serial.println("Downloading from: " + imageUrl);
    logEvent(("Downloading from: " + imageUrl).c_str());
    if (!http.begin(client, imageUrl)) {
        Serial.println("ERROR: http.begin() failed");
        logEvent("ERROR: http.begin() failed");
        return false;
    }
    
    int httpCode = http.GET();
    if (httpCode != 200) {
        Serial.printf("ERROR: HTTP GET code=%d\n", httpCode);
        logEvent(("ERROR: HTTP GET code=" + String(httpCode)).c_str());
        http.end();
        return false;
    }
    
    WiFiClient *stream = http.getStreamPtr();
    if (!stream) {
        Serial.println("ERROR: No stream from HTTP");
        logEvent("ERROR: No stream from HTTP");
        http.end();
        return false;
    }
    
    SdFile outFile;
    if (!outFile.open(localPath.c_str(), O_WRITE | O_CREAT | O_TRUNC)) {
        Serial.println("ERROR: Could not open file on SD");
        logEvent("ERROR: Could not open file on SD");
        http.end();
        return false;
    }
    
    uint8_t buff[512];
    int totalBytes = 0;
    unsigned long lastReadTime = millis();
    while ((millis() - lastReadTime) < 10000) {
        size_t availableBytes = stream->available();
        if (availableBytes > 0) {
            lastReadTime = millis();
            int bytesRead = stream->readBytes((char*)buff, (availableBytes > sizeof(buff)) ? sizeof(buff) : availableBytes);
            outFile.write(buff, bytesRead);
            totalBytes += bytesRead;
        } else if (!stream->connected()) {
            break;
        }
        delay(1);
    }
    
    outFile.close();
    http.end();
    
    String downloadMsg = "Downloaded " + String(totalBytes) + " bytes -> " + localPath;
    Serial.println(downloadMsg);
    logEvent(downloadMsg.c_str());
    return (totalBytes > 0);
}

/**
 * Fetch a BMP image from the server, save it to SD, render it, and then schedule deep sleep.
 * Battery information is included in the server request.
 * 
 * In normal operation, once the image is successfully rendered, no overlay is added.
 */
void fetchAndDisplayImage() {
    setState(STATE_FETCHING_IMAGE);
    
    if (!checkAndReconnectWifi()) {
        setState(STATE_ERROR, ERROR_WIFI_CONNECT);
        Serial.println("ERROR: Not connected to Wi-Fi");
        logEvent("ERROR: Not connected to Wi-Fi");
        delay(5000);
        ESP.restart();
        return;
    }
    
    // Generate a unique device ID from the ESP32 MAC address.
    uint64_t chipid = ESP.getEfuseMac();
    char deviceId[17];
    sprintf(deviceId, "%04X%08X", (uint16_t)(chipid >> 32), (uint32_t)chipid);
    String deviceUuid = String(deviceId);
    Serial.println("Device UUID: " + deviceUuid);
    logEvent(("Device UUID: " + deviceUuid).c_str());
    
    // Prepare the server URL.
    String serverUrl = "https://fridge-thing-production.up.railway.app/api/devices/" + deviceUuid + "/display";
    WiFiClientSecure client;
    client.setInsecure();
    
    // POST request with firmware version and battery info.
    HTTPClient http;
    http.setTimeout(10000);
    http.begin(client, serverUrl);
    http.addHeader("Content-Type", "application/json");
    
    StaticJsonDocument<256> doc;
    doc["current_fw_ver"] = currentFirmwareVersion;
    double voltage = display.readBattery();
    float batteryPercent = voltageToPercent(voltage);
    doc["battery_pct"] = batteryPercent;
    doc["battery_voltage"] = voltage;
    
    String body;
    serializeJson(doc, body);
    int httpCode = http.POST(body);
    if (httpCode != 200) {
        String errMsg = "ERROR: POST code=" + String(httpCode);
        Serial.println(errMsg);
        logEvent(errMsg.c_str());
        http.end();
        setState(STATE_ERROR, ERROR_SERVER_CONNECT);
        delay(5000);
        // Schedule a retry after 1 minute.
        esp_sleep_enable_timer_wakeup(60 * 1000000ULL);
        esp_sleep_enable_ext0_wakeup(GPIO_NUM_36, 0);
        setState(STATE_SLEEPING);
        delay(1000);
        esp_deep_sleep_start();
        return;
    }
    
    String resp = http.getString();
    Serial.println("Server response: " + resp);
    logEvent(("Server response: " + resp).c_str());
    
    StaticJsonDocument<512> respDoc;
    DeserializationError err = deserializeJson(respDoc, resp);
    http.end();
    if (err) {
        Serial.println("ERROR: JSON parse failed");
        logEvent("ERROR: JSON parse failed");
        setState(STATE_ERROR, ERROR_SERVER_CONNECT);
        delay(5000);
        ESP.restart();
        return;
    }
    
    String imageUrl = respDoc["image_url"].as<String>();
    long nextWakeSec = respDoc["next_wake_secs"].as<long>();
    if (imageUrl.startsWith("http://")) {
        imageUrl.replace("http://", "https://");
    }
    
    // Download the image.
    const String localPath = "/temp.bmp";
    if (!downloadToSD(imageUrl, localPath, client)) {
        Serial.println("ERROR: Could not download image");
        logEvent("ERROR: Could not download image");
        setState(STATE_ERROR, ERROR_IMAGE_DOWNLOAD);
        delay(5000);
        // Schedule a retry after 1 minute.
        esp_sleep_enable_timer_wakeup(60 * 1000000ULL);
        esp_sleep_enable_ext0_wakeup(GPIO_NUM_36, 0);
        setState(STATE_SLEEPING);
        delay(1000);
        esp_deep_sleep_start();
        return;
    }
    
    // Render the downloaded image.
    setState(STATE_DISPLAYING_IMAGE);
    Serial.println("Rendering image...");
    logEvent("Rendering image...");
    bool ok = display.drawImage(localPath.c_str(), 0, 0);
    if (!ok) {
        Serial.println("ERROR: drawImage failed");
        logEvent("ERROR: drawImage failed");
        setState(STATE_ERROR, ERROR_IMAGE_DOWNLOAD);
        delay(5000);
        ESP.restart();
        return;
    }
    
    // At this point, the image is fully rendered.
    // We do not update the display with a "Sleeping..." overlay.
    display.display();
    
    String sleepMsg = "Sleeping for " + String(nextWakeSec) + " seconds...";
    Serial.println(sleepMsg);
    logEvent(sleepMsg.c_str());
    
    // Store next wake-up info persistently.
    preferences.begin("sleep", false);
    preferences.putLong("nextWake", nextWakeSec);
    preferences.end();
    
    esp_sleep_enable_timer_wakeup(nextWakeSec * 1000000ULL);
    esp_sleep_enable_ext0_wakeup(GPIO_NUM_36, 0);
    
    // Instead of calling setState(STATE_SLEEPING) which would update the display,
    // we log the state and then immediately enter deep sleep.
    logEvent("Entering sleep mode.");
    delay(1000);
    esp_deep_sleep_start();
}

/**
 * Check for OTA firmware updates. If a new version is available, trigger the update.
 */
void checkOTAUpdate() {
    setState(STATE_CHECKING_UPDATE);
    Serial.println("Checking for OTA update...");
    logEvent("Checking for OTA update...");
    
    if (!checkAndReconnectWifi()) {
        Serial.println("Wi-Fi not connected; skipping OTA check");
        logEvent("Wi-Fi not connected; skipping OTA update check");
        return;
    }
    
    WiFiClientSecure client;
    client.setInsecure();
    
    HTTPClient http;
    http.setTimeout(10000);
    if (http.begin(client, versionCheckURL)) {
        int httpCode = http.GET();
        if (httpCode == 200) {
            String newVersion = http.getString();
            newVersion.trim();
            Serial.println("Latest firmware: " + newVersion);
            logEvent(("Latest firmware: " + newVersion).c_str());
            if (newVersion != String(currentFirmwareVersion)) {
                Serial.println("New firmware available. Starting OTA update...");
                logEvent("New firmware available. Starting OTA update...");
                setState(STATE_UPDATING_FW);
                t_httpUpdate_return ret = httpUpdate.update(client, firmwareURL);
                switch(ret) {
                    case HTTP_UPDATE_FAILED:
                        Serial.printf("OTA failed (%d): %s\n", httpUpdate.getLastError(), httpUpdate.getLastErrorString().c_str());
                        logEvent(("OTA failed (" + String(httpUpdate.getLastError()) + "): " + httpUpdate.getLastErrorString()).c_str());
                        setState(STATE_ERROR, ERROR_OTA_UPDATE);
                        delay(5000);
                        break;
                    case HTTP_UPDATE_NO_UPDATES:
                        Serial.println("No OTA updates available.");
                        logEvent("No OTA updates available.");
                        break;
                    case HTTP_UPDATE_OK:
                        // Device will reboot automatically.
                        break;
                }
            } else {
                Serial.println("Firmware up-to-date.");
                logEvent("Firmware up-to-date.");
            }
        } else {
            Serial.printf("OTA check HTTP code: %d\n", httpCode);
            logEvent(("OTA check HTTP code: " + String(httpCode)).c_str());
        }
        http.end();
    } else {
        Serial.println("Failed to initiate OTA HTTP connection.");
        logEvent("Failed to initiate OTA HTTP connection.");
    }
}

/**
 * Start the captive portal so the user can input Wi-Fi credentials.
 */
void startCaptivePortal() {
    setState(STATE_CAPTIVE_PORTAL);
    Serial.println("Starting Captive Portal...");
    logEvent("Starting Captive Portal");
    
    WiFi.mode(WIFI_AP);
    WiFi.softAP(apSSID, apPassword);
    
    dnsServer.start(53, "*", WiFi.softAPIP());
    
    if (!MDNS.begin("fridgething")) {
        Serial.println("Error starting mDNS");
        logEvent("Error starting mDNS");
    }
    
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
            logEvent("Wi-Fi credentials updated via captive portal.");
            delay(2000);
            ESP.restart();
        } else {
            request->send(400, "text/plain", "Missing SSID or Password");
        }
    });
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
    server.begin();
    captivePortalStartTime = millis();
}

/**
 * Setup: initialize display, SD card, Wi-Fi, and state; decide whether to start normal operation or captive portal.
 */
void setup() {
    Serial.begin(115200);
    Serial.println("\n\nFridge Thing starting up...");
    logEvent("Fridge Thing starting up...");
    
    // Optionally restore previous state info.
    preferences.begin("state", false);
    int lastState = preferences.getInt("lastState", STATE_INITIALIZING);
    int lastError = preferences.getInt("errorCode", ERROR_NONE);
    preferences.end();
    
    currentState = STATE_INITIALIZING;
    
    display.begin();
    updateStateDisplay(); // Show initial status.
    
    // Initialize SD card.
    sdCardAvailable = display.sdCardInit();
    if (!sdCardAvailable) {
        Serial.println("SD init failed. Continuing without SD storage.");
        logEvent("SD init failed.");
        setState(STATE_ERROR, ERROR_SD_CARD);
        delay(3000);
    }
    
    // Check battery before continuing.
    double voltage = display.readBattery();
    float batteryPercent = voltageToPercent(voltage);
    String battMsg = "Battery: " + String(batteryPercent, 1) + "% (" + String(voltage, 2) + "V)";
    Serial.println(battMsg);
    logEvent(battMsg.c_str());
    if (batteryPercent < BATTERY_CRITICAL_PCT) {
        Serial.println("CRITICAL: Battery too low!");
        logEvent("CRITICAL: Battery too low!");
        setState(STATE_ERROR, ERROR_LOW_BATTERY);
        delay(5000);
        esp_sleep_enable_timer_wakeup(3600 * 1000000ULL); // Sleep for 1 hour.
        esp_sleep_enable_ext0_wakeup(GPIO_NUM_36, 0);
        setState(STATE_SLEEPING);
        delay(1000);
        esp_deep_sleep_start();
        return;
    }
    
    // Read stored Wi-Fi credentials.
    preferences.begin("wifi", false);
    String storedSSID = preferences.getString("ssid", "");
    String storedPass = preferences.getString("password", "");
    preferences.end();
    
    // Check wakeup reason (cold boot vs. wake from sleep).
    esp_sleep_wakeup_cause_t wakeup_reason = esp_sleep_get_wakeup_cause();
    if (wakeup_reason == ESP_SLEEP_WAKEUP_TIMER || wakeup_reason == ESP_SLEEP_WAKEUP_EXT0) {
        Serial.println("Woke from sleep");
        logEvent("Woke from sleep");
        if (storedSSID != "") {
            setState(STATE_CONNECTING_WIFI);
            WiFi.mode(WIFI_STA);
            WiFi.begin(storedSSID.c_str(), storedPass.c_str());
            
            unsigned long startTime = millis();
            int attempts = 0;
            while (WiFi.status() != WL_CONNECTED && attempts < 15) {
                if (millis() - startTime > WIFI_CONNECT_TIMEOUT) break;
                delay(1000);
                Serial.print(".");
                attempts++;
            }
            if (WiFi.status() == WL_CONNECTED) {
                Serial.println("\nWi-Fi connected!");
                logEvent("Wi-Fi connected on wakeup.");
                Serial.print("IP: ");
                Serial.println(WiFi.localIP());
                checkOTAUpdate();
                fetchAndDisplayImage();
                return;
            } else {
                Serial.println("\nWi-Fi connection failed on wakeup.");
                logEvent("Wi-Fi connection failed on wakeup.");
                setState(STATE_ERROR, ERROR_WIFI_CONNECT);
                delay(3000);
            }
        }
    } else {
        // Cold boot: try to connect with stored credentials.
        if (storedSSID != "") {
            setState(STATE_CONNECTING_WIFI);
            WiFi.mode(WIFI_STA);
            WiFi.begin(storedSSID.c_str(), storedPass.c_str());
            
            unsigned long startTime = millis();
            int attempts = 0;
            while (WiFi.status() != WL_CONNECTED && attempts < 15) {
                if (millis() - startTime > WIFI_CONNECT_TIMEOUT) break;
                delay(1000);
                Serial.print(".");
                attempts++;
            }
            if (WiFi.status() == WL_CONNECTED) {
                Serial.println("\nWi-Fi connected!");
                logEvent("Wi-Fi connected on cold boot.");
                Serial.print("IP: ");
                Serial.println(WiFi.localIP());
                checkOTAUpdate();
                fetchAndDisplayImage();
                return;
            }
        }
    }
    
    // If Wi-Fi connection fails or no credentials, start captive portal.
    startCaptivePortal();
}

/**
 * Main loop: process DNS requests and manage captive portal timeout.
 */
void loop() {
    dnsServer.processNextRequest();
    
    if (WiFi.getMode() == WIFI_AP && captivePortalStartTime > 0) {
        unsigned long elapsed = millis() - captivePortalStartTime;
        if (elapsed >= CAPTIVE_PORTAL_TIMEOUT_MS) {
            Serial.println("Captive portal timeout; going to sleep.");
            logEvent("Captive portal timeout; going to sleep.");
            setState(STATE_SLEEPING);
            esp_sleep_enable_timer_wakeup(30ULL * 1000000ULL); // Sleep for 30 seconds.
            esp_sleep_enable_ext0_wakeup(GPIO_NUM_36, 0);
            delay(1000);
            esp_deep_sleep_start();
        }
    }
}