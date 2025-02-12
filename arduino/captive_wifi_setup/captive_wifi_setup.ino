// --- WORKAROUND DEFINES ---
#define FS_NO_GLOBALS
#define INKPLATE_NO_SD

#include <FS.h>                     // Include FS first (with FS_NO_GLOBALS defined)
#include <WiFi.h>
#include <ESPAsyncWebServer.h>
#include <Preferences.h>
#include <DNSServer.h>
#include <ESPmDNS.h>
#include <Inkplate.h>               // With INKPLATE_NO_SD defined, this skips SD code
#include <esp_sleep.h>             // For deep sleep functions

// Create a default Inkplate object.
Inkplate display;

// Create other global objects.
Preferences preferences;
AsyncWebServer server(80);
DNSServer dnsServer;

// Access Point settings.
const char *apSSID = "FridgeThing";
const char *apPassword = "";  // Open network

// HTML content for the Wi-Fi setup page with improved styling.
const char *htmlSetupPage = R"rawliteral(
<!DOCTYPE html>
<html>
  <head>
    <meta charset="UTF-8">
    <title>Fridge Thing - Wi-Fi Setup</title>
    <style>
      body {
        font-family: Arial, sans-serif;
        font-size: 20px;
        background-color: #f8f8f8;
        text-align: center;
        margin: 20px;
      }
      h2 {
        font-size: 28px;
        margin-bottom: 20px;
      }
      input[type="text"],
      input[type="password"] {
        font-size: 20px;
        padding: 10px;
        margin: 10px 0;
        width: 80%;
        max-width: 400px;
      }
      input[type="submit"] {
        font-size: 20px;
        padding: 10px 20px;
        margin-top: 20px;
      }
    </style>
  </head>
  <body>
    <h2>Enter Wi-Fi Details</h2>
    <form action="/setup" method="post">
      <input type="text" name="ssid" placeholder="Wi-Fi Name"><br>
      <input type="password" name="password" placeholder="Wi-Fi Password"><br>
      <input type="submit" value="Save">
    </form>
  </body>
</html>
)rawliteral";

// HTML snippet used by captive portal endpoints to force redirection.
const char *htmlRedirect = R"rawliteral(
<!DOCTYPE html>
<html>
  <head>
    <meta http-equiv="refresh" content="0; url=http://fridgething.local/">
    <title>Redirecting...</title>
  </head>
  <body>
    <p>Redirecting to Wi-Fi Setup...</p>
  </body>
</html>
)rawliteral";

// Forward declaration of the function.
void startCaptivePortal();

// Timeout for captive portal (5 minutes = 300,000 ms)
static const unsigned long CAPTIVE_PORTAL_TIMEOUT_MS = 300000;

// Track when we start the captive portal
unsigned long captivePortalStartTime = 0;

void setup() {
  Serial.begin(115200);

  // Uncomment for testing: clear stored credentials to force captive portal mode.
  // preferences.begin("wifi", false);
  // preferences.clear();      // Clear all stored key/value pairs.
  // preferences.end();
  
  // Always open preferences for reading stored credentials
  preferences.begin("wifi", false);
  String storedSSID = preferences.getString("ssid", "");
  String storedPassword = preferences.getString("password", "");
  preferences.end();

  // Initialize the Inkplate display.
  display.begin();
  display.clearDisplay();
  display.setTextColor(BLACK);
  display.setTextSize(4);
  display.setCursor(10, 20);
  display.print("Booting up...");
  display.display();

  // Attempt to connect with stored credentials if available
  if (storedSSID != "") {
    Serial.print("Connecting to Wi-Fi: ");
    Serial.println(storedSSID);
    display.clearDisplay();
    display.setTextColor(BLACK);
    display.setTextSize(2);
    display.setCursor(10, 20);
    display.print("Connecting to Wi-Fi...");
    display.display();

    WiFi.mode(WIFI_STA);
    WiFi.begin(storedSSID.c_str(), storedPassword.c_str());

    int attempts = 0;
    while (WiFi.status() != WL_CONNECTED && attempts < 15) {
      delay(1000);
      Serial.print(".");
      attempts++;
    }

    if (WiFi.status() == WL_CONNECTED) {
      Serial.println("\nConnected to Wi-Fi!");
      Serial.print("IP Address: ");
      Serial.println(WiFi.localIP());

      display.clearDisplay();
      display.setTextColor(BLACK);
      display.setTextSize(2);
      display.setCursor(10, 20);
      display.print("Wi-Fi Connected!");
      display.setTextSize(1);
      display.setCursor(10, 50);
      display.print(WiFi.localIP());
      display.display();
      
      // Connected: we can exit setup here
      return;
    } else {
      Serial.println("\nFailed to connect to stored Wi-Fi.");
    }
  }

  // If no valid Wi‑Fi credentials or connection failed: start the captive portal
  startCaptivePortal();
}

void startCaptivePortal() {
  Serial.println("\nStarting Captive Portal...");
  WiFi.mode(WIFI_AP);
  WiFi.softAP(apSSID, apPassword);

  // Allow time for the AP to initialize
  delay(500);

  // Print the AP IP for debugging
  Serial.print("AP IP address: ");
  Serial.println(WiFi.softAPIP());

  // Update the display with instructions
  display.clearDisplay();
  display.setTextColor(BLACK);
  display.setTextSize(5);
  display.setCursor(10, 10);
  display.print("Wi-Fi Setup");
  display.setTextSize(2);
  display.setCursor(10, 80);
  display.print("Connect to the network 'FridgeThing'");
  display.setCursor(10, 120);
  display.print("If not prompted automatically, visit:");
  display.setCursor(10, 145);
  display.print("http://fridgething.local/");
  display.display();

  // Start a DNS server to redirect all requests to the Inkplate’s IP
  dnsServer.start(53, "*", WiFi.softAPIP());

  // Start mDNS so that the friendly URL works
  if (!MDNS.begin("fridgething")) {
    Serial.println("Error starting mDNS");
  }

  // --- Define Web Routes for the Captive Portal ---
  // Main page with the Wi-Fi setup form
  server.on("/", HTTP_GET, [](AsyncWebServerRequest *request) {
    request->send(200, "text/html", htmlSetupPage);
  });

  // Handle form submissions
  server.on("/setup", HTTP_POST, [](AsyncWebServerRequest *request) {
    if (request->hasParam("ssid", true) && request->hasParam("password", true)) {
      String newSSID = request->getParam("ssid", true)->value();
      String newPassword = request->getParam("password", true)->value();

      // Save new credentials to Preferences
      preferences.begin("wifi", false);
      preferences.putString("ssid", newSSID);
      preferences.putString("password", newPassword);
      preferences.end();

      request->send(200, "text/html", "<html><body><h2>Wi-Fi Configured!</h2><p>Restarting...</p></body></html>");
      delay(2000);
      ESP.restart();
    } else {
      request->send(400, "text/plain", "Missing SSID or Password");
    }
  });

  // Redirect endpoints used for captive portal detection to the main setup page
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

  // Start the HTTP server
  server.begin();

  // Record the time we started the captive portal
  captivePortalStartTime = millis();
}

void loop() {
  dnsServer.processNextRequest();
  
  // If in AP mode, check how long we've been up and go to sleep if timed out
  if (WiFi.getMode() == WIFI_AP && captivePortalStartTime > 0) {
    unsigned long elapsed = millis() - captivePortalStartTime;
    if (elapsed >= CAPTIVE_PORTAL_TIMEOUT_MS) {
      Serial.println("No Wi-Fi config received; going to deep sleep...");

      // Enable a timed wake-up if desired (example: wake after 30s)
      esp_sleep_enable_timer_wakeup(30ULL * 1000000ULL); // 30 seconds

      // Clear (or dim) the display before sleeping if you want
      display.clearDisplay();
      display.setCursor(10, 50);
      display.setTextColor(BLACK);
      display.setTextSize(2);
      display.print("Going to sleep...");
      display.display();
      delay(1000);

      // Enter deep sleep
      esp_deep_sleep_start();
    }
  }
}