#define FS_NO_GLOBALS

#include <WiFi.h>
#include <Preferences.h>
#include <ESPmDNS.h>
#include <HTTPClient.h>
#include <WiFiClientSecure.h>
#include <ArduinoJson.h>
#include <Inkplate.h>
#include <esp_sleep.h>
#include <HTTPUpdate.h>
#include <esp_wifi.h>

// Forward declaration to prevent implicit prototype issues
bool readWiFiCredentialsFromSD(String &ssid, String &password);

// RTC_DATA_ATTR makes this variable persist across deep sleep cycles
RTC_DATA_ATTR int bootCount = 0;

// Create Inkplate object
Inkplate display;

// Global objects
Preferences preferences;

// OTA configuration
const char* currentFirmwareVersion = "1.4";  // Current firmware version
const char* versionCheckURL = "https://s3.us-west-1.amazonaws.com/bjork.love/fridge-thing-firmware/version.txt";
const char* firmwareURL = "https://s3.us-west-1.amazonaws.com/bjork.love/fridge-thing-firmware/firmware.ino.bin";

// State tracking and error handling constants
#define STATE_INITIALIZING     0
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
 * Format numbers with leading zeros for timestamps.
 */
String formatNumber(int num) {
    if (num < 10)
        return "0" + String(num);
    return String(num);
}

/**
 * Log an event to "log.txt" on the SD card with a timestamp from the RTC.
 */
void logEvent(const char* message) {
    if (!sdCardAvailable) {
        Serial.println("SD card not available for logging.");
        return;
    }
    
    SdFile logFile;
    // Open the log file in append mode.
    if (!logFile.open("/log.txt", O_WRITE | O_CREAT | O_APPEND)) {
        Serial.println("ERROR: Could not open log file.");
        return;
    }
    
    // Get current date and time from RTC
    display.rtcGetRtcData();
    
    uint8_t second = display.rtcGetSecond();
    uint8_t minute = display.rtcGetMinute();
    uint8_t hour = display.rtcGetHour();
    uint8_t day = display.rtcGetDay();
    uint8_t month = display.rtcGetMonth();
    uint8_t year = display.rtcGetYear();
    
    // Format: YYYY-MM-DD HH:MM:SS: message
    char timestamp[20];
    sprintf(timestamp, "20%02d-%02d-%02d %02d:%02d:%02d", 
            year, month, day, hour, minute, second);
    
    String logLine = String(timestamp) + ": " + message + "\n";
    logFile.write((const uint8_t*)logLine.c_str(), logLine.length());
    logFile.close();
    
    // Also print to Serial for debugging.
    Serial.println(logLine);
}

/**
 * Update the display with device status information.
 */
void updateStateDisplay(bool fullRefresh = true) {
    if (fullRefresh) {
        display.clearDisplay();
    }
    display.setTextColor(BLACK);
    display.setTextSize(2);
    display.setCursor(10, 10);
    
    if (currentState == STATE_ERROR) {
        display.print("Error: ");
        switch (errorCode) {
            case ERROR_WIFI_CONNECT:
                display.print("WiFi Connection Failed");
                display.setCursor(10, 50);
                display.print("Check wifi.txt on SD card");
                break;
            case ERROR_SERVER_CONNECT:
                display.print("Server Connection Failed");
                break;
            case ERROR_IMAGE_DOWNLOAD:
                display.print("Image Download Failed");
                break;
            case ERROR_SD_CARD:
                display.print("SD Card Error");
                break;
            case ERROR_OTA_UPDATE:
                display.print("Update Failed");
                break;
            case ERROR_LOW_BATTERY:
                display.print("Low Battery");
                break;
            default:
                display.print("Unknown Error");
                break;
        }
    } else if (currentState == STATE_INITIALIZING) {
        display.print("Initializing...");
    } else if (currentState == STATE_CONNECTING_WIFI) {
        display.print("Connecting to WiFi...");
        display.setCursor(10, 50);
        display.print("Using wifi.txt settings");
    }
    
    display.display();
}

/**
 * Set the current state and error code, store them persistently, and log the event.
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
    
    String ssid = "";
    String password = "";
    
    // First try to read from SD card
    if (sdCardAvailable && readWiFiCredentialsFromSD(ssid, password)) {
        // Successfully read from SD card
    } else {
        // Fall back to preferences
        preferences.begin("wifi", false);
        ssid = preferences.getString("ssid", "");
        password = preferences.getString("password", "");
        preferences.end();
    }
    
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
                Serial.println("Multiple failures; check wifi.txt file on SD card.");
                logEvent("Multiple Wi-Fi failures; check wifi.txt file on SD card.");
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
 * Read WiFi credentials from the SD card.
 * Format of the file should be:
 * NETWORK=YourNetworkName
 * PASSWORD=YourPassword
 * 
 * Returns true if credentials were successfully read.
 */
bool readWiFiCredentialsFromSD(String &ssid, String &password) {
    if (!sdCardAvailable) {
        Serial.println("ERROR: SD card not available");
        logEvent("ERROR: SD card not available for reading WiFi credentials");
        return false;
    }
    
    SdFile wifiFile;
    if (!wifiFile.open("/wifi.txt", O_READ)) {
        Serial.println("INFO: wifi.txt not found on SD card");
        logEvent("INFO: wifi.txt not found on SD card");
        return false;
    }
    
    char buffer[256];
    size_t bytesRead = 0;
    String fileContent = "";
    
    // Read the file content
    while ((bytesRead = wifiFile.read(buffer, sizeof(buffer) - 1)) > 0) {
        buffer[bytesRead] = '\0';
        fileContent += String(buffer);
    }
    wifiFile.close();
    
    // Parse the file content line by line
    int networkPos = fileContent.indexOf("NETWORK=");
    int passwordPos = fileContent.indexOf("PASSWORD=");
    
    if (networkPos >= 0) {
        int ssidEndPos = fileContent.indexOf('\n', networkPos);
        if (ssidEndPos < 0) ssidEndPos = fileContent.length();
        ssid = fileContent.substring(networkPos + 8, ssidEndPos);
        ssid.trim();
    } else {
        Serial.println("ERROR: NETWORK not found in wifi.txt");
        logEvent("ERROR: NETWORK not found in wifi.txt");
        return false;
    }
    
    if (passwordPos >= 0) {
        int passwordEndPos = fileContent.indexOf('\n', passwordPos);
        if (passwordEndPos < 0) passwordEndPos = fileContent.length();
        password = fileContent.substring(passwordPos + 9, passwordEndPos);
        password.trim();
    } else {
        Serial.println("ERROR: PASSWORD not found in wifi.txt");
        logEvent("ERROR: PASSWORD not found in wifi.txt");
        return false;
    }
    
    Serial.println("Successfully read WiFi credentials from SD card");
    // Don't log the actual credentials for security reasons
    logEvent("Successfully read WiFi credentials from SD card");
    
    // Optionally save to preferences as a backup
    preferences.begin("wifi", false);
    preferences.putString("ssid", ssid);
    preferences.putString("password", password);
    preferences.end();
    
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
        // Disable WiFi to save battery before deep sleep.
        WiFi.disconnect();
        WiFi.mode(WIFI_OFF);
        esp_wifi_stop();
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
        // Disable WiFi to save battery before deep sleep.
        WiFi.disconnect();
        WiFi.mode(WIFI_OFF);
        esp_wifi_stop();
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
    
    // At this point, the image is fully rendered and remains on screen.
    display.display();
    
    String sleepMsg = "Sleeping for " + String(nextWakeSec) + " seconds...";
    Serial.println(sleepMsg);
    logEvent(sleepMsg.c_str());
    
    // Store next wake-up info persistently.
    preferences.begin("sleep", false);
    preferences.putLong("nextWake", nextWakeSec);
    preferences.end();
    
    // Enable timer wakeup for the next scheduled update.
    esp_sleep_enable_timer_wakeup(nextWakeSec * 1000000ULL);
    // Enable external wakeup on GPIO36 (wake-up button).
    esp_sleep_enable_ext0_wakeup(GPIO_NUM_36, 0);
    
    logEvent("Entering sleep mode.");
    // Disable WiFi to save battery before deep sleep.
    WiFi.disconnect();
    WiFi.mode(WIFI_OFF);
    esp_wifi_stop();
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
 * Setup: initialize display, RTC, SD card, Wi-Fi, state, and proceed with normal operation.
 * Also, increment the boot count and log the wake-up cause.
 */
void setup() {
    Serial.begin(115200);
    // Disable Bluetooth to save power, as it's not used.
    btStop();
    Serial.println("\n\nFridge Thing starting up...");
    
    // Initialize display and RTC
    display.begin();
    display.rtcGetRtcData();
    
    // If the RTC does not appear to be set (year < 20), set a default time.
    if (display.rtcGetYear() < 20) {
        Serial.println("RTC not set; initializing with default time and date...");
        display.rtcSetTime(14, 10, 0); // Hours, Minutes, Seconds
        display.rtcSetDate(6, 3, 8, 25); // Weekday (1 = Monday), Month, Day, Year
        
        // Optional: RTC calibration
        display.rtcSetInternalCapacitor(RTC_12_5PF);
        display.rtcSetClockOffset(1, -63); // Adjust offset as needed
    }
    
    // Increment boot count (persistent across deep sleep)
    bootCount++;
    
    // Initialize SD card before logging.
    sdCardAvailable = display.sdCardInit();
    if (!sdCardAvailable) {
        Serial.println("SD init failed. Continuing without SD storage.");
    }
    
    logEvent("Fridge Thing starting up...");
    logEvent(("Boot count: " + String(bootCount)).c_str());
    
    // Determine wakeup cause.
    esp_sleep_wakeup_cause_t wakeup_reason = esp_sleep_get_wakeup_cause();
    String wakeupMsg;
    switch (wakeup_reason) {
        case ESP_SLEEP_WAKEUP_EXT0:
            wakeupMsg = "Wakeup caused by external button";
            break;
        case ESP_SLEEP_WAKEUP_TIMER:
            wakeupMsg = "Wakeup caused by timer";
            break;
        default:
            wakeupMsg = "Wakeup cause unknown";
            break;
    }
    logEvent(wakeupMsg.c_str());
    
    currentState = STATE_INITIALIZING;
    
    // Check battery before continuing.
    double voltage = display.readBattery();
    float batteryPercent = voltageToPercent(voltage);
    String battMsg = "Battery: " + String(batteryPercent, 1) + "% (" + String(voltage, 2) + "V)";
    logEvent(battMsg.c_str());
    if (batteryPercent < BATTERY_CRITICAL_PCT) {
        logEvent("CRITICAL: Battery too low!");
        setState(STATE_ERROR, ERROR_LOW_BATTERY);
        delay(5000);
        // Disable WiFi to save battery before deep sleep.
        WiFi.disconnect();
        WiFi.mode(WIFI_OFF);
        esp_wifi_stop();
        esp_sleep_enable_timer_wakeup(3600 * 1000000ULL); // Sleep for 1 hour.
        esp_sleep_enable_ext0_wakeup(GPIO_NUM_36, 0);
        setState(STATE_SLEEPING);
        delay(1000);
        esp_deep_sleep_start();
        return;
    }
    
    // Try to read WiFi credentials from SD card
    String ssid = "";
    String password = "";
    bool hasWiFiCredentials = false;
    
    if (sdCardAvailable) {
        hasWiFiCredentials = readWiFiCredentialsFromSD(ssid, password);
    }
    
    // If SD card credentials are not available, try to use stored preferences
    if (!hasWiFiCredentials) {
        // Read stored Wi-Fi credentials from preferences
        preferences.begin("wifi", false);
        ssid = preferences.getString("ssid", "");
        password = preferences.getString("password", "");
        preferences.end();
        
        if (ssid != "") {
            hasWiFiCredentials = true;
        }
    }
    
    if (!hasWiFiCredentials) {
        // No WiFi credentials found anywhere
        setState(STATE_ERROR, ERROR_WIFI_CONNECT);
        display.clearDisplay();
        display.setTextSize(2);
        display.setCursor(10, 10);
        display.print("WiFi Setup Required");
        display.setCursor(10, 50);
        display.print("Please create wifi.txt");
        display.setCursor(10, 90);
        display.print("on SD card with:");
        display.setCursor(10, 130);
        display.print("NETWORK=YourWiFiName");
        display.setCursor(10, 170);
        display.print("PASSWORD=YourPassword");
        display.display();
        
        // Sleep for 5 minutes then retry
        delay(5000);
        logEvent("No WiFi credentials; sleeping for 5 minutes");
        esp_sleep_enable_timer_wakeup(300 * 1000000ULL); // 5 minutes
        esp_sleep_enable_ext0_wakeup(GPIO_NUM_36, 0);
        esp_deep_sleep_start();
        return;
    }
    
    // Try to connect with available credentials
    setState(STATE_CONNECTING_WIFI);
    WiFi.mode(WIFI_STA);
    WiFi.begin(ssid.c_str(), password.c_str());
    
    unsigned long startTime = millis();
    int attempts = 0;
    Serial.print("Connecting to WiFi");
    while (WiFi.status() != WL_CONNECTED && attempts < 15) {
        if (millis() - startTime > WIFI_CONNECT_TIMEOUT) break;
        delay(1000);
        Serial.print(".");
        attempts++;
    }
    Serial.println();
    
    if (WiFi.status() == WL_CONNECTED) {
        logEvent("Wi-Fi connected successfully");
        Serial.print("Connected to WiFi. IP: ");
        Serial.println(WiFi.localIP());
        
        // Check for OTA updates
        checkOTAUpdate();
        
        // Fetch and display the image
        fetchAndDisplayImage();
    } else {
        // WiFi connection failed
        logEvent("Wi-Fi connection failed");
        setState(STATE_ERROR, ERROR_WIFI_CONNECT);
        
        display.clearDisplay();
        display.setTextSize(2);
        display.setCursor(10, 10);
        display.print("WiFi Connection Failed");
        display.setCursor(10, 50);
        display.print("Check wifi.txt");
        display.setCursor(10, 90);
        display.print("Network: ");
        display.print(ssid);
        display.display();
        
        // Sleep for 1 minute then retry
        delay(5000);
        logEvent("Sleeping for 1 minute before retry");
        esp_sleep_enable_timer_wakeup(60 * 1000000ULL); // 1 minute
        esp_sleep_enable_ext0_wakeup(GPIO_NUM_36, 0);
        setState(STATE_SLEEPING);
        delay(1000);
        esp_deep_sleep_start();
    }
}

/**
 * Main loop: this will never run in normal operation as the device will enter deep sleep.
 * Only included for completeness.
 */
void loop() {
    // This should never execute in normal operation as the device
    // will either fetch an image and go to sleep or restart on error.
    delay(1000);
}