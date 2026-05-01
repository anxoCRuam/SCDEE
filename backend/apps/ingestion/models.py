"""
Recognition results model.

Stores raw recognition results per zone per page for audit
and debugging. No new tables needed — results are stored as
JSON on ExamPage, but we define the result structure here.

References: RF-9.3 through RF-9.9
"""

# Recognition results are stored as JSON on ExamPage.recognized_data
# (added via migration). No ORM model needed.
#
# Structure of recognized_data (JSONField on ExamPage):
# {
#     "zones": [
#         {
#             "zone_id": "uuid",
#             "zone_type": "QR|OCR_TEXT|OCR_NUMBER|CHECKBOX",
#             "attribute": "exam_qr|name|dni|nia|...",
#             "value": "recognized text or decoded data",
#             "confidence": 0.95,
#             "raw_value": "original OCR output before cleanup",
#         }
#     ],
#     "qr_payload": {
#         "org_id": "uuid",
#         "exam_id": "uuid",
#         "model_id": "uuid",
#         "page_number": 1,
#         "checksum": "abc12345",
#         "valid": true
#     },
#     "student_match": {
#         "user_id": "uuid",
#         "confidence": 0.92,
#         "matched_by": "nia|dni|name",
#     }
# }
