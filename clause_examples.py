import csv
import sys

EXAMPLES = [
    ("The premises cover 85 acres.", "Parcel size and boundaries"),
    ("No construction above 35 °C.", "Operational restriction"),
    ("The lease expires on June 30.", "Term or deadline"),
    ("Maintain vegetation buffers.", "Environmental obligation"),
    ("Rent shall be paid monthly.", "Other"),
]

if __name__ == "__main__":
    writer = csv.writer(sys.stdout)
    writer.writerow(["text", "category"])
    writer.writerows(EXAMPLES)
