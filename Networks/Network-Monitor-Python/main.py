import packetsniffer
import threading
from usagemonitor import UsageMonitor
from tkinter import *
from tkinter import scrolledtext


def main():
    window = Tk()

    window.title("Network monitor")
    window.geometry('800x600')
    lbl = Label(window, text="Packet Sniffing")

    lbl.grid(column=0, row=0, pady=10, padx=10)
    txt = Listbox(window, width=95, height=30)
    txt.see("end")
    txt.grid(column=0, row=1, pady=10, padx=10)
    try:
        t1 = threading.Thread(target=packetsniffer.start_sniffing, args=(txt,))
        t2 = threading.Thread(target=UsageMonitor(lbl).start, args=())
        t1.start()
        t2.start()
    except IOError:
        print("Error: unable to start thread")
    window.mainloop()


main()
