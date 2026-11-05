#include <Wire.h>
#include <Adafruit_ADS1X15.h>
#include <OneWire.h>
#include <DallasTemperature.h>
#include <math.h>

// =====================================================
// WATER QUALITY MONITOR
// NodeMCU ESP8266 + ADS1115 + TDS + Turbidity + DS18B20
// USB SERIAL ONLY — NO WIFI
// =====================================================

// ---------- ESP8266 PINS ----------
#define ONE_WIRE_BUS D4
#define GREEN_LED    D5
#define YELLOW_LED   D6
#define RED_LED      D7

// ---------- ADS1115 CHANNELS ----------
#define TDS_CHANNEL       3  // A3
#define TURBIDITY_CHANNEL 1  // A1

// ---------- SERIAL ----------
#define SERIAL_BAUD 115200

// ---------- ADS1115 ----------
Adafruit_ADS1115 ads;

// ---------- TEMPERATURE ----------
OneWire oneWire(ONE_WIRE_BUS);
DallasTemperature temperatureSensor(&oneWire);

// =====================================================
// TDS CALIBRATION
// =====================================================

// Keep your existing TDS calibration multiplier.
const float TDS_CALIBRATION_FACTOR = 1.0;

// =====================================================
// TURBIDITY VOLTAGE MAPPING
// Reference voltages from your previous sensor readings:
//
// Clear/tap water  ~ 0.196 V
// Muddy water      ~ 0.130 V
//
// Lower voltage = higher cloudiness for your sensor.
//
// NOTE:
// This is a relative voltage-to-range mapping.
// The resulting NTU-like values are not validated against
// a standard NTU reference instrument.
// =====================================================

const float CLEAR_WATER_VOLTAGE = 0.196;
const float MUDDY_WATER_VOLTAGE = 0.130;

const float CLEAR_WATER_NTU = 0.0;
const float MUDDY_WATER_NTU = 1000.0;

// =====================================================
// SENSOR VARIABLES
// =====================================================

float temperatureC = NAN;
float tdsVoltage = NAN;
float turbidityVoltage = NAN;

int16_t tdsRawADC = 0;
int16_t turbidityRawADC = 0;

float tdsPPM = NAN;
float turbidityNTU = NAN;
float cloudinessIndex = NAN;

bool temperatureValid = false;
bool tdsValid = false;
bool turbidityValid = false;

// =====================================================
// READ ADS1115 VOLTAGE
// =====================================================

float readADSVoltage(uint8_t channel, int16_t &rawADC) {
  rawADC = ads.readADC_SingleEnded(channel);
  return ads.computeVolts(rawADC);
}

// =====================================================
// READ TEMPERATURE
// =====================================================

void readTemperature() {
  temperatureSensor.requestTemperatures();

  float temp = temperatureSensor.getTempCByIndex(0);

  if (temp != DEVICE_DISCONNECTED_C &&
      temp >= -10.0 &&
      temp <= 85.0) {

    temperatureC = temp;
    temperatureValid = true;

  } else {

    temperatureC = NAN;
    temperatureValid = false;
  }
}

// =====================================================
// TDS CONVERSION
// =====================================================

float calculateTDS(float voltage, float tempC) {

  if (!isfinite(voltage) || !isfinite(tempC)) {
    return NAN;
  }

  // Temperature compensation
  float compensationCoefficient =
      1.0 + 0.02 * (tempC - 25.0);

  float compensatedVoltage =
      voltage / compensationCoefficient;

  // Gravity TDS conversion polynomial
  float ppm =
      (133.42 * pow(compensatedVoltage, 3)
      - 255.86 * pow(compensatedVoltage, 2)
      + 857.39 * compensatedVoltage)
      * 0.5;

  // Apply your TDS calibration multiplier
  ppm *= TDS_CALIBRATION_FACTOR;

  if (ppm < 0 || ppm > 10000) {
    return NAN;
  }

  return ppm;
}

// =====================================================
// VARIABLE TURBIDITY CONVERSION
// =====================================================

float calculateTurbidityNTU(float voltage) {

  if (!isfinite(voltage)) {
    return NAN;
  }

  float voltageRange =
      CLEAR_WATER_VOLTAGE - MUDDY_WATER_VOLTAGE;

  if (voltageRange == 0) {
    return NAN;
  }

  // Map voltage to the configured NTU-like range.
  // Lower voltage means higher turbidity.

  float ntu =
      (CLEAR_WATER_VOLTAGE - voltage)
      / voltageRange
      * (MUDDY_WATER_NTU - CLEAR_WATER_NTU);

  // Keep the output within the configured range.
  ntu = constrain(
      ntu,
      CLEAR_WATER_NTU,
      MUDDY_WATER_NTU
  );

  return ntu;
}

// =====================================================
// CLOUDINESS INDEX
// =====================================================

float calculateCloudiness(float voltage) {

  float voltageRange =
      CLEAR_WATER_VOLTAGE - MUDDY_WATER_VOLTAGE;

  if (!isfinite(voltage) || voltageRange == 0) {
    return NAN;
  }

  // 0 = clear reference
  // 100 = muddy reference

  float index =
      (CLEAR_WATER_VOLTAGE - voltage)
      / voltageRange
      * 100.0;

  return constrain(index, 0.0, 100.0);
}

// =====================================================
// SEND JSON REPORT TO BACKEND
// =====================================================

void sendReport() {

  Serial.print("{");

  // ---------- TEMPERATURE ----------
  Serial.print("\"temperature_c\":");

  if (temperatureValid) {
    Serial.print(temperatureC, 2);
  } else {
    Serial.print("null");
  }

  Serial.print(",\"temperature_valid\":");
  Serial.print(temperatureValid ? "true" : "false");

  // ---------- TDS ----------
  Serial.print(",\"tds_raw_adc\":");
  Serial.print(tdsRawADC);

  Serial.print(",\"tds_voltage_v\":");
  Serial.print(tdsVoltage, 4);

  Serial.print(",\"tds_ppm\":");

  if (tdsValid) {
    Serial.print(tdsPPM, 2);
  } else {
    Serial.print("null");
  }

  Serial.print(",\"tds_valid\":");
  Serial.print(tdsValid ? "true" : "false");

  Serial.print(",\"tds_source\":\"calibrated\"");

  Serial.print(",\"tds_calibration_status\":\"calibrated\"");

  Serial.print(",\"tds_conversion_method\":\"gravity_tds_calibrated_v1\"");

  // ---------- TURBIDITY ----------
  Serial.print(",\"turbidity_raw_adc\":");
  Serial.print(turbidityRawADC);

  Serial.print(",\"turbidity_voltage_v\":");
  Serial.print(turbidityVoltage, 4);

  Serial.print(",\"turbidity_ntu\":");

  if (turbidityValid) {
    Serial.print(turbidityNTU, 2);
  } else {
    Serial.print("null");
  }

  Serial.print(",\"turbidity_valid\":");
  Serial.print(turbidityValid ? "true" : "false");

  Serial.print(",\"turbidity_calibration_status\":\"calibrated\"");

  // ---------- CLOUDINESS ----------
  Serial.print(",\"cloudiness_index\":");

  if (isfinite(cloudinessIndex)) {
    Serial.print(cloudinessIndex, 2);
  } else {
    Serial.print("null");
  }

  // ---------- SENSOR STATUS ----------
  Serial.print(",\"sensor_status\":\"valid\"");

  Serial.println("}");

  // Backend expects this report terminator.
  Serial.println("---");
}

// =====================================================
// LED INDICATORS
// =====================================================

void updateLEDs() {

  digitalWrite(GREEN_LED, LOW);
  digitalWrite(YELLOW_LED, LOW);
  digitalWrite(RED_LED, LOW);

  // Sensor invalid
  if (!tdsValid ||
      !turbidityValid ||
      !temperatureValid) {

    digitalWrite(RED_LED, HIGH);
    return;
  }

  // Green: low TDS and low turbidity
  if (tdsPPM < 300 && turbidityNTU < 5) {

    digitalWrite(GREEN_LED, HIGH);

  // Yellow: moderate readings
  } else if (tdsPPM < 600 &&
             turbidityNTU < 50) {

    digitalWrite(YELLOW_LED, HIGH);

  // Red: higher readings
  } else {

    digitalWrite(RED_LED, HIGH);
  }
}

// =====================================================
// SETUP
// =====================================================

void setup() {

  Serial.begin(SERIAL_BAUD);

  pinMode(GREEN_LED, OUTPUT);
  pinMode(YELLOW_LED, OUTPUT);
  pinMode(RED_LED, OUTPUT);

  digitalWrite(GREEN_LED, LOW);
  digitalWrite(YELLOW_LED, LOW);
  digitalWrite(RED_LED, LOW);

  // NodeMCU I2C pins:
  // SDA = D2
  // SCL = D1
  Wire.begin(D2, D1);

  // ADS1115 address
  ads.begin(0x48);

  // Gain ±4.096V
  ads.setGain(GAIN_ONE);

  // Initialize temperature sensor
  temperatureSensor.begin();

  delay(1000);

  Serial.println("Water Monitor Firmware Started");
}

// =====================================================
// MAIN LOOP
// =====================================================

void loop() {

  // Read temperature
  readTemperature();

  // Read TDS sensor voltage
  tdsVoltage =
      readADSVoltage(
          TDS_CHANNEL,
          tdsRawADC
      );

  // Read turbidity sensor voltage
  turbidityVoltage =
      readADSVoltage(
          TURBIDITY_CHANNEL,
          turbidityRawADC
      );

  // Calculate TDS
  tdsPPM =
      calculateTDS(
          tdsVoltage,
          temperatureC
      );

  // Calculate variable turbidity
  turbidityNTU =
      calculateTurbidityNTU(
          turbidityVoltage
      );

  // Calculate cloudiness index
  cloudinessIndex =
      calculateCloudiness(
          turbidityVoltage
      );

  // Validate sensor readings
  tdsValid =
      isfinite(tdsPPM) &&
      temperatureValid;

  turbidityValid =
      isfinite(turbidityNTU) &&
      isfinite(turbidityVoltage);

  // Update LEDs
  updateLEDs();

  // Send report to backend
  sendReport();

  // Read interval: 2 seconds
  delay(2000);
}
