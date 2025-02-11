#include <WiFi.h>
#include <ESPAsyncWebServer.h>
#include <Preferences.h>
#include <DNSServer.h>

// Create objects
Preferences preferences;
AsyncWebServer server(80);
DNSServer dnsServer;

// AP settings
const char *apSSID = "Inkplate_Setup";
const char *apPassword = "";  // Open network

void setup() {
    Serial.begin(115200);
    
    // Load stored Wi-Fi credentials
    preferences.begin("wifi", false);
    String ssid = preferences.getString("ssid", "");
    String password = preferences.getString("password", "");

    WiFi.mode(WIFI_STA);
    if (ssid != "") {
        WiFi.begin(ssid.c_str(), password.c_str());
        Serial.print("Connecting to Wi-Fi");

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
            return;
        }
    }

    // Start Captive Portal if connection fails
    startCaptivePortal();
}

void startCaptivePortal() {
    Serial.println("\nStarting Captive Portal...");
    WiFi.mode(WIFI_AP);
    WiFi.softAP(apSSID, apPassword);

    dnsServer.start(53, "*", WiFi.softAPIP()); // Redirect all web requests

    server.on("/", HTTP_GET, [](AsyncWebServerRequest *request) {
        request->send(200, "text/html", R"rawliteral(
            <html>
            <head><title>Wi-Fi Setup</title></head>
            <body>
            <h2>Enter Wi-Fi Details</h2>
            <form action="/setup" method="post">
                SSID: <input type="text" name="ssid"><br>
                Password: <input type="password" name="password"><br>
                <input type="submit" value="Save">
            </form>
            </body>
            </html>
        )rawliteral");
    });

    server.on("/setup", HTTP_POST, [](AsyncWebServerRequest *request) {
        if (request->hasParam("ssid", true) && request->hasParam("password", true)) {
            String newSSID = request->getParam("ssid", true)->value();
            String newPassword = request->getParam("password", true)->value();

            preferences.putString("ssid", newSSID);
            preferences.putString("password", newPassword);
            
            request->send(200, "text/html", "Wi-Fi Configured! Restarting...");
            delay(2000);
            ESP.restart();
        } else {
            request->send(400, "text/plain", "Missing SSID or Password");
        }
    });

    server.begin();
}

void loop() {
    dnsServer.processNextRequest();
}
