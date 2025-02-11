// --- WORKAROUND DEFINES ---
// Prevent global definitions from the FS library (avoiding conflicts)
// and disable SD support for Inkplate (since we don't need it for this test).
#define FS_NO_GLOBALS
#define INKPLATE_NO_SD

#include <FS.h>                     // Include FS first (with FS_NO_GLOBALS defined)
#include <WiFi.h>
#include <ESPAsyncWebServer.h>
#include <Preferences.h>
#include <DNSServer.h>
#include <ESPmDNS.h>
#include <Inkplate.h>               // With INKPLATE_NO_SD defined, this skips SD code

// Create a default Inkplate object.
Inkplate display;

// Create other global objects.
Preferences preferences;
AsyncWebServer server(80);
DNSServer dnsServer;

// Access Point settings.
const char *apSSID = "Inkplate_Setup";
const char *apPassword = "";  // Open network

// HTML content for the Wi-Fi setup page with improved styling.
const char *htmlSetupPage = R"rawliteral(
<!DOCTYPE html>
<html>
  <head>
    <meta charset="UTF-8">
    <title>Wi-Fi Setup</title>
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
      <input type="text" name="ssid" placeholder="Wi-Fi SSID"><br>
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
    <meta http-equiv="refresh" content="0; url=http://setup.local/">
    <title>Redirecting...</title>
  </head>
  <body>
    <p>Redirecting to Wi-Fi Setup...</p>
  </body>
</html>
)rawliteral";

// Forward declaration of the function.
void startCaptivePortal();

void setup() {
  Serial.begin(115200);

  // For testing: clear stored credentials to force captive portal mode.
  preferences.begin("wifi", false);
  preferences.clear();      // Clear all stored key/value pairs.
  preferences.end();
  preferences.begin("wifi", false);
  
  // Initialize the Inkplate display.
  display.begin();
  display.clearDisplay();
  display.setTextColor(BLACK);
  display.setTextSize(2);
  display.setCursor(10, 20);
  display.print("Starting Inkplate...");
  display.display();

  // Load stored Wi‑Fi credentials.
  String storedSSID = preferences.getString("ssid", "");
  String storedPassword = preferences.getString("password", "");

  WiFi.mode(WIFI_STA);
  if (storedSSID != "") {
    Serial.print("Connecting to Wi‑Fi: ");
    Serial.println(storedSSID);
    display.clearDisplay();
    display.setTextColor(BLACK);
    display.setTextSize(2);
    display.setCursor(10, 20);
    display.print("Connecting to Wi‑Fi...");
    display.display();

    WiFi.begin(storedSSID.c_str(), storedPassword.c_str());

    int attempts = 0;
    while (WiFi.status() != WL_CONNECTED && attempts < 15) {
      delay(1000);
      Serial.print(".");
      attempts++;
    }

    if (WiFi.status() == WL_CONNECTED) {
      Serial.println("\nConnected to Wi‑Fi!");
      Serial.print("IP Address: ");
      Serial.println(WiFi.localIP());

      display.clearDisplay();
      display.setTextColor(BLACK);
      display.setTextSize(2);
      display.setCursor(10, 20);
      display.print("Wi‑Fi Connected!");
      display.setTextSize(1);
      display.setCursor(10, 50);
      display.print(WiFi.localIP());
      display.display();
      return;  // Connected: exit setup.
    }
    else {
      Serial.println("\nFailed to connect to stored Wi‑Fi.");
    }
  }

  // No valid Wi‑Fi credentials or connection failed: start the captive portal.
  startCaptivePortal();
}

void startCaptivePortal() {
  Serial.println("\nStarting Captive Portal...");
  WiFi.mode(WIFI_AP);
  WiFi.softAP(apSSID, apPassword);

  // Allow time for the AP to initialize.
  delay(500);

  // Print the AP IP for debugging.
  Serial.print("AP IP address: ");
  Serial.println(WiFi.softAPIP());

  // Update the display with instructions for the user.
  display.clearDisplay();
  display.setTextColor(BLACK);
  display.setTextSize(2);
  display.setCursor(10, 10);
  display.print("Network Setup Mode");
  display.setTextSize(1);
  display.setCursor(10, 40);
  display.print("Connect to 'Inkplate_Setup'");
  display.setCursor(10, 60);
  display.print("then visit:");
  display.setCursor(10, 80);
  display.print("http://setup.local/");
  display.setCursor(10, 100);
  display.print("Waiting for connection...");
  display.display();

  // Start a DNS server to redirect all requests to the Inkplate’s IP.
  dnsServer.start(53, "*", WiFi.softAPIP());

  // Start mDNS so that the friendly URL works.
  if (!MDNS.begin("setup")) {
    Serial.println("Error starting mDNS");
  }

  // --- Define Web Routes for the Captive Portal ---
  // Main page with the Wi-Fi setup form.
  server.on("/", HTTP_GET, [](AsyncWebServerRequest *request) {
    request->send(200, "text/html", htmlSetupPage);
  });

  // Handle form submissions.
  server.on("/setup", HTTP_POST, [](AsyncWebServerRequest *request) {
    if (request->hasParam("ssid", true) && request->hasParam("password", true)) {
      String newSSID = request->getParam("ssid", true)->value();
      String newPassword = request->getParam("password", true)->value();

      preferences.putString("ssid", newSSID);
      preferences.putString("password", newPassword);

      request->send(200, "text/html", "<html><body><h2>Wi-Fi Configured!</h2><p>Restarting...</p></body></html>");
      delay(2000);
      ESP.restart();
    } else {
      request->send(400, "text/plain", "Missing SSID or Password");
    }
  });

  // Redirect endpoints used for captive portal detection to the main setup page.
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
}

void loop() {
  dnsServer.processNextRequest();
}