"""Regressions for source counterexamples found during the authorized apply review."""
from pathlib import Path
import unittest

from vibiomir.cleaning_rules import load_cleaning_config
from vibiomir.cleaning_transform import process_version
from test_stage4_transform import parent

PROFILE = Path(__file__).resolve().parents[1] / "configs/cleaning_stage4_reviewed.json"
BOOKING = ("Để đặt lịch khám tại viện, Quý khách vui lòng bấm số HOTLINE hoặc đặt lịch trực tiếp TẠI ĐÂY . "
    "Tải và đặt lịch khám tự động trên ứng dụng MyVinmec để quản lý, theo dõi lịch và đặt hẹn mọi lúc mọi nơi ngay trên ứng dụng.")


@unittest.skipUnless(PROFILE.is_file(), "No reviewed local profile has been created")
class ReviewedProfileTest(unittest.TestCase):
    def config(self):
        return load_cleaning_config(PROFILE)

    def test_reviewed_profile_applies_verified_booking_footer_only(self):
        prefix = "Original clinical article and source context.\n" * 10
        result = process_version(parent(prefix + BOOKING, "vinmec.com"), self.config(), mode="apply")
        self.assertEqual(result["version"]["removed_chars"], len(BOOKING))
        self.assertEqual(result["regions"][0]["text"], prefix)
        self.assertEqual(result["transformations"][0]["decision"], "applied_removal")

    def test_booking_line_with_attached_clinical_heading_is_retained(self):
        prefix = "Original clinical article and source context.\n" * 10
        source = prefix + BOOKING + " Lưu ý khi thực hiện:\nKhông tự ngừng thuốc."
        result = process_version(parent(source, "vinmec.com"), self.config(), mode="apply")
        self.assertEqual(result["version"]["removed_chars"], 0)
        self.assertEqual(result["regions"][0]["text"], source)

    def test_thanhnien_fact_heading_is_not_misclassified_as_a_related_link(self):
        source = ("Original article about pregnancy and childcare.\n" * 8
            + "Mẹ nghĩ:\nNên ăn cho hai người.\n>> Sự thực là:\n"
            + "Đây là nội dung chính của phần giải thích, cần giữ nguyên.")
        result = process_version(parent(source, "thanhnien.vn"), self.config(), mode="apply")
        self.assertEqual(result["version"]["removed_chars"], 0)
        self.assertEqual(result["regions"][0]["text"], source)


if __name__ == "__main__":
    unittest.main()
