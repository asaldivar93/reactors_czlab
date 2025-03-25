"""test IS libraries"""
import time
from librpiplc import rpiplc

def analog_write_pwm(val):
    rpiplc.pin_mode("Q0.5", rpiplc.OUTPUT)
    rpiplc.analog_write_set_frequency("Q0.5", 24)
    rpiplc.analog_write("Q0.5", val)
    
if __name__=="__main__":
	try:
		rpiplc.init("RPIPLC_V6", "RPIPLC_58")
		while True:
			time.sleep(4)
			analog_write_pwm(1000)
			time.sleep(4)
			analog_write_pwm(2000)
			time.sleep(4)
			analog_write_pwm(4000)
			
	except KeyboardInterrupt:
		analog_write_pwm(0)
		
