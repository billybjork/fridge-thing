#include <WiFi.h>

const char* ssid = "Steamboat Willie";
const char* password = "!!!!!!!!!!";

void setup() {
  Serial.begin(115200);
  delay(3000); // Small delay to ensure Serial Monitor is ready

  Serial.println("WiFi Test Only: Starting...");

  WiFi.begin(ssid, password);
  int attempts = 0;

  while (WiFi.status() != WL_CONNECTED && attempts < 20) {
    delay(500);
    Serial.print(".");
    attempts++;
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\nWiFi connected!");
    Serial.print("IP Address: ");
    Serial.println(WiFi.localIP().toString());
  } else {
    Serial.println("\nWiFi connection failed!");
  }
}

void loop() {
  // Do nothing
}
