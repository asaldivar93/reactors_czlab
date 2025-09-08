"""test IS libraries"""
import time
from librpiplc import rpiplc

def analog_write_pwm(val):
    rpiplc.pin_mode("Q0.5", rpiplc.OUTPUT)
    rpiplc.analog_write_set_frequency("Q0.5", 5)
    rpiplc.analog_write("Q0.5", val)
    
if __name__=="__main__":
    pin = "Q2.7"
    try:
        rpiplc.init("RPIPLC_V6", "RPIPLC_58")
        rpiplc.pin_mode(pin, rpiplc.OUTPUT)
        rpiplc.analog_write_set_frequency(pin, 100)
        while True:
            rpiplc.analog_write(pin, 4095)
            time.sleep(0.1)
            rpiplc.analog_write(pin, 4095)
            time.sleep(0.1)
            rpiplc.analog_write(pin, 4095)
            time.sleep(0.1)
            rpiplc.analog_write(pin, 4095)
    except KeyboardInterrupt:
        rpiplc.analog_write(pin, 0)

