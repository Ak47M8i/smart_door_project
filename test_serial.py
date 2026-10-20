import time
import serial

# Test direct connection to COM6
try:
    print("Attempting to connect to COM6...")
    arduino = serial.Serial("COM6", 9600, timeout=1)
    time.sleep(2)  # Wait for connection to settle
    print("Connected successfully!")

    print("Sending UNLOCK command...")
    arduino.write(b"UNLOCK\n")
    time.sleep(1)

    # Read response
    while arduino.in_waiting > 0:
        print("Arduino replied:", arduino.readline().decode("utf-8").strip())

    arduino.close()
    print("Test finished.")
except Exception as e:
    print(f"Error: {e}")