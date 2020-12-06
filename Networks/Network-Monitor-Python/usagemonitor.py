import psutil
import time


class UsageMonitor:

    def __init__(self, text_box):
        self.text_box = text_box

    def start(self):
        old_value = 0

        while True:
            new_value = psutil.net_io_counters().bytes_sent + psutil.net_io_counters().bytes_recv
            if old_value:
                self.send_stat(new_value - old_value)
            old_value = new_value
            time.sleep(1)

    def convert_to_mbit(self, value):
        return value / 1024. / 1024. * 8

    def send_stat(self, value):
        self.text_box.configure(text=("Network Usage: %0.3f" % (value / 1024. / 1024.)) + " MB/s")
