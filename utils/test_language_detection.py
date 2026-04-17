import unittest
import sys
import os

# Add root directory to sys.path to import jira_translator and server
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jira_translator import is_english as jt_is_english
from server.server import is_english as server_is_english

class TestLanguageDetection(unittest.TestCase):
    def setUp(self):
        self.test_cases = [
            # text, expected_is_english
            ("This is a simple english text.", True),
            ("这是一个简单的中文文本。", False),
            ("（2）如果用户超过30分钟未处理（715异常保持在APP界面显示），机器人降级清洁，执行扫地任务。\n这样的好处是：715的异常用户能进行加水从而解除此异常，机器人也能顺利完成清洁任务（不会停止任务）", False),
            ("The user clicks login and sees the 登录 button", True),
            ("Project ABC", True),
            ("APP", True),
        ]

    def test_jira_translator_is_english(self):
        print("\n--- Testing jira_translator.py is_english ---")
        failures = []
        for text, expected in self.test_cases:
            result = jt_is_english(text)
            print(f"Text: {text[:30]:<30} | Expected: {expected!s:<5} | Actual: {result!s:<5}")
            if result != expected:
                failures.append((text, expected, result))
        self.assertEqual(len(failures), 0, f"Failed cases: {failures}")

    def test_server_is_english(self):
        print("\n--- Testing server.py is_english ---")
        failures = []
        for text, expected in self.test_cases:
            result = server_is_english(text)
            print(f"Text: {text[:30]:<30} | Expected: {expected!s:<5} | Actual: {result!s:<5}")
            if result != expected:
                failures.append((text, expected, result))
        self.assertEqual(len(failures), 0, f"Failed cases: {failures}")

if __name__ == '__main__':
    unittest.main()
