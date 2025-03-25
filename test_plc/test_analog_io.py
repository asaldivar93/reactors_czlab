from librpiplc import rpiplc

def analog_read_write():
    rpiplc.pin_mode("A0.5", rpiplc.OUTPUT)

    rpiplc.analog_write("A0.5", 2048) # 2.5v Output
    rpiplc.delay(2000)
    rpiplc.pin_mode("I0.12", rpiplc.INPUT)
    read_value=rpiplc.analog_read("I0.12") # 0 - 2047
    print("The I0.12 is reading: {}".format(read_value))
	

    
if __name__=="__main__":
	try:
		rpiplc.init("RPIPLC_V6", "RPIPLC_58")
		while True:
			analog_read_write()
			
	except KeyboardInterrupt:
		rpiplc.analog_write("A0.5", 0)
