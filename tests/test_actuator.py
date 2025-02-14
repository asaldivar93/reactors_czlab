test1 = _TimerControl(2.3, 5.1, 150)
test2 = _TimerControl(2.3, 5.1, 150)
test3 = _TimerControl(2.3, 2.3, 150)
test4 = _ManualControl(50)
test5 = _ManualControl(50)
test6 = _ManualControl(10)

test1 != test2
test1 != test3
test1 != test4
test4 != test5
test4 != test6

test6 = _OnBoundariesControl(1.1, 2.1, 150)
test7 = _OnBoundariesControl(1.1, 2.1, 150, reversed=True)

test6.get_value(2.2)
test7.get_value(1.1)

test8 = _PidControl(35)
