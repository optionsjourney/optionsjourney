import logging

from optionsjourney import logs


def test_the_log_file_is_in_the_data_folder_and_added_once(tmp_path):
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        path = logs.start_file(tmp_path)
        assert path == tmp_path / "logs" / "optionsjourney.log"
        logs.start_file(tmp_path)
        added = [h for h in root.handlers if h not in before]
        assert len(added) == 1
        logging.getLogger("optionsjourney.test").warning("hello log")
        added[0].flush()
        assert "hello log" in path.read_text(encoding="utf-8")
    finally:
        for handler in [h for h in root.handlers if h not in before]:
            root.removeHandler(handler)
            handler.close()
