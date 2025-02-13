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

// Create Inkplate object
Inkplate display;

// Global objects
Preferences preferences;
AsyncWebServer server(80);
DNSServer dnsServer;

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

// Forward declaration
void startCaptivePortal();

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
    while (stream->connected() || stream->available()) {
        size_t size = stream->available();
        if (size) {
            int c = stream->readBytes((char*)buff, (size > sizeof(buff)) ? sizeof(buff) : size);
            outFile.write(buff, c);
            totalBytes += c;
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
 */
void fetchAndDisplayImage() {
    if (WiFi.status() != WL_CONNECTED) {
        Serial.println("ERROR: Not connected to Wi-Fi");
        return;
    }

    // The server route: /api/devices/{device_uuid}/display
    String deviceUuid = "1234-test-device";
    String serverUrl = "http://192.168.4.137:8000/api/devices/" + deviceUuid + "/display"; // Update with production server URL
    WiFiClient client;

    // 1) POST to get {image_url, next_wake_secs}
    HTTPClient http;
    http.setTimeout(10000);
    http.begin(client, serverUrl);
    http.addHeader("Content-Type", "application/json");

    StaticJsonDocument<256> doc;
    doc["current_fw_ver"] = "0.1";
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

    // 2) Download file to SD using a plain WiFiClient (not WiFiClientSecure)
    const String localPath = "/temp.bmp";
    if (!downloadToSD(imageUrl, localPath, client)) {
        Serial.println("ERROR: Could not download image");
        return;
    }

    // 3) Use drawImage on the local file
    Serial.println("Rendering downloaded image with drawImage...");
    bool ok = display.drawImage(localPath.c_str(), 0, 0);
    if (!ok) {
        Serial.println("ERROR: drawImage failed");
    } else {
        Serial.println("BMP image displayed successfully.");
    }

    display.display();

    // 4) Deep sleep
    Serial.printf("Going to deep sleep for %ld seconds...\n", nextWakeSec);
    esp_sleep_enable_timer_wakeup(nextWakeSec * 1000000ULL);
    esp_deep_sleep_start();
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

    // Inkplate init
    display.begin();

    // Inkplate sdCardInit
    if (!display.sdCardInit()) {
        Serial.println("SD init failed. We'll keep going, but can't store images!");
    }

    display.clearDisplay();
    display.setTextColor(BLACK);
    display.setTextSize(4);
    display.setCursor(10, 20);
    display.print("Booting...");
    display.display();

    // Attempt Wi-Fi
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

            // Immediately fetch and display image
            fetchAndDisplayImage();
            return;
        }
    }

    // If Wi-Fi fails, start captive portal
    startCaptivePortal();
}

/**
 * Start captive portal if no Wi-Fi credentials exist or connect fails.
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
    display.print("Connect to 'FridgeThing'");
    display.setCursor(10, 120);
    display.print("Visit: http://fridgething.local/");
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
        if (elapsed >= 300000UL) {  // 5 minutes
            Serial.println("No Wi-Fi config received; going to deep sleep...");

            // Sleep for 30 seconds, or any appropriate fallback
            esp_sleep_enable_timer_wakeup(30ULL * 1000000ULL);
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