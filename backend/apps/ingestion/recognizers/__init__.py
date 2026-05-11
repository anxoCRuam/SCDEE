"""
Recognition plugins: QR, OCR (text/number), and checkbox recognizers.

Each recognizer implements BaseRecognizer.recognize(image_bytes) → (value, confidence).
The dispatcher invokes the appropriate recognizer based on the zone type.
"""
