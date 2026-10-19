// Demonstration generator for Priority-Based Data Storage System.
// Sends one newline-delimited record per interval.
// Format: {module_id,reading_1,reading_2,...}
// Priority is assigned by the backend, not by the Arduino.

unsigned long lastSend = 0;
const unsigned long intervalMs = 1000;

void setup() {
  Serial.begin(9600);
  randomSeed(analogRead(A0));
}

void loop() {
  if (millis() - lastSend >= intervalMs) {
    lastSend = millis();

    int id = random(1,3);

    if (id == 1){
      // Module 101: illustrative temperature, humidity, gas, pressure.
      float temperature = random(180, 360) / 10.0;
      float humidity = random(300, 901) / 10.0;
      int gas = random(250, 950);
      float pressure = random(980, 1051) / 10.0;

      Serial.print("{101,");
      Serial.print(temperature, 1); Serial.print(",");
      Serial.print(humidity, 1); Serial.print(",");
      Serial.print(gas); Serial.print(",");
      Serial.print(pressure, 1);
      Serial.println("}");
    }

    else if (id == 2){
      // Uncomment this block to also simulate module 102.
      int motion = random(0, 101);
      float distance = random(10, 500) / 10.0;
      float tilt = random(-900, 901) / 10.0;
      float battery = random(330, 421) / 100.0;
      Serial.print("{102,"); Serial.print(motion); Serial.print(",");
      Serial.print(distance, 1); Serial.print(",");
      Serial.print(tilt, 1); Serial.print(",");
      Serial.print(battery, 2); Serial.println("}");
    }
  }
}
