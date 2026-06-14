#define RELAY D6

void setup() {
  pinMode(RELAY, OUTPUT);
}

void loop() {
  digitalWrite(RELAY, LOW);
  delay(2000);

  digitalWrite(RELAY, HIGH);
  delay(2000);
}