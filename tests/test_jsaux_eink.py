import unittest

from PIL import Image

from jsaux_eink import (
    FRAMEBUFFER_SIZE,
    HEIGHT,
    WIDTH,
    build_data_packet,
    build_query_packet,
    framebuffer_to_image,
    make_test_pattern,
    parse_device_info,
    parse_pnp_id,
    parse_response_frame,
    prepare_image,
)


class ProtocolTests(unittest.TestCase):
    def test_query_packet(self):
        self.assertEqual(build_query_packet(), bytes.fromhex("ac0000ca"))

    def test_parse_device_info(self):
        response = bytes.fromhex("91000100028801e0006c4d19")
        info = parse_device_info(response)

        self.assertFalse(info.is_dual_screen)
        self.assertEqual(info.color_mode, 0)
        self.assertEqual(info.width, 648)
        self.assertEqual(info.height, 480)
        self.assertEqual(info.packet_size, 108)
        self.assertEqual(info.battery, 77)
        self.assertTrue(info.is_supported)

    def test_parse_response_rejects_wrong_footer(self):
        self.assertIsNone(parse_response_frame(bytes.fromhex("91020000"), 0x02))

    def test_parse_pnp_id(self):
        pnp_id = parse_pnp_id(bytes.fromhex("028a2466820100"))

        self.assertEqual(pnp_id.vendor_source, 2)
        self.assertEqual(pnp_id.vendor_id, 0x248A)
        self.assertEqual(pnp_id.product_id, 0x8266)
        self.assertEqual(pnp_id.product_version, 1)

    def test_data_packet_has_big_endian_index_and_padding(self):
        packet = build_data_packet(0x1234, b"\xaa\xbb", 4)
        self.assertEqual(packet, bytes.fromhex("ac021234aabb0000ca"))


class FramebufferTests(unittest.TestCase):
    def test_solid_patterns_have_expected_polarity(self):
        self.assertEqual(make_test_pattern("black").tobytes(), b"\x00" * FRAMEBUFFER_SIZE)
        self.assertEqual(make_test_pattern("white").tobytes(), b"\xff" * FRAMEBUFFER_SIZE)

    def test_prepare_image_is_exact_size_and_msb_first(self):
        source = Image.new("1", (WIDTH, HEIGHT), 1)
        source.putpixel((0, 0), 0)
        source.putpixel((7, 0), 0)

        framebuffer, preview = prepare_image(source, dither=False)

        self.assertEqual(len(framebuffer), FRAMEBUFFER_SIZE)
        self.assertEqual(framebuffer[0], 0b01111110)
        self.assertEqual(preview.size, (WIDTH, HEIGHT))

    def test_framebuffer_round_trip(self):
        raw = make_test_pattern("checkerboard").tobytes()
        restored = framebuffer_to_image(raw)

        self.assertEqual(restored.size, (WIDTH, HEIGHT))
        self.assertEqual(restored.tobytes(), raw)

    def test_raw_frame_size_is_enforced(self):
        with self.assertRaises(ValueError):
            framebuffer_to_image(b"\x00")


if __name__ == "__main__":
    unittest.main()
