import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ChannelGuidanceTests(unittest.TestCase):
    def test_plow_phone_guidance_is_direct_and_webchat_specific_guidance_is_conditional(self):
        prompt = (ROOT / "openclaw/workspace/AGENTS.md").read_text()
        paragraph = next(
            part for part in prompt.split("\n\n")
            if "In a Plow phone/text conversation" in part
        )
        phone_guidance = paragraph.split("Only when")[0]
        self.assertIn("Send the STL file directly in this", phone_guidance)
        self.assertIn("conversation and I'll analyze it.", phone_guidance)
        for browser_term in ("WebChat", "paperclip", "browser file picker", "file selector"):
            self.assertNotIn(browser_term.lower(), phone_guidance.lower())
        self.assertIn("Only when the current conversation is\nWebChat", paragraph)
        self.assertIn("mention its upload selector", paragraph)

    def test_webchat_intake_guidance_remains_available(self):
        analysis_skill = (ROOT / "openclaw/workspace/skills/analyze-stl/SKILL.md").read_text()
        operations_skill = (ROOT / "openclaw/workspace/skills/print-farm-operations/SKILL.md").read_text()
        self.assertIn("one STL through the WebChat file selector", analysis_skill)
        self.assertIn("Only in WebChat, offer upload", operations_skill)
        self.assertIn("attachment selector", operations_skill)


if __name__ == "__main__":
    unittest.main()
