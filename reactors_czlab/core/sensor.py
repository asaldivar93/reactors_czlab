"""Dummy sensor class"""
import random


class Sensor:
    def __init__(self, id):
        self.id = id
        self.value = 0
        self.lb = 0
        self.ub = 100
        self.EUlb = 0
        self.EUub = 40
        self.unit_symbol = "oC"
        self.description = "degree celsius"

    def read(self):
        self.value = random.gauss(35, 1)
        return self.value
