"""Dummy sensor class"""
import random

class Sensor:
    def __init__(self, id):
        self.id = id
        self.value = 0

    def read(self):
        self.value = random.gauss(35, 1)
