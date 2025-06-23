from __future__ import annotations

import asyncio
import random

from nicegui import app, ui


class SensorInfo:
    def __init__(self, idx) -> None:
        self.id = idx
        self.value = 0.0


ph_sensors = [SensorInfo(i) for i in range(3)]
do_sensors = [SensorInfo(i) for i in range(3)]


async def opc_update():
    while True:
        for s in ph_sensors:
            s.value = round(random.gauss(7, 0.5), 2)
        for s in do_sensors:
            s.value = round(random.gauss(7, 0.5), 2)
        await asyncio.sleep(1)


ui.label("Calibration")
ui.separator()
main = ui.column()


def cal_page(sensor: SensorInfo):
    main.clear()
    with main:
        ui.label(f"Sensor_{sensor.id}")


with ui.left_drawer().style("background-color: #ebf1fa"):
    ui.label("Sensors")
    ui.separator()
    with ui.row():
        with ui.column():
            for sensor in ph_sensors:
                with ui.button(on_click=lambda sensor=sensor: cal_page(sensor)):
                    ui.label("pH_0::")
                    ui.label().bind_text_from(sensor, "value")

        with ui.column():
            for sensor in ph_sensors:
                with ui.button(on_click=lambda sensor=sensor: cal_page(sensor)):
                    ui.label("pH_0::")
                    ui.label().bind_text_from(sensor, "value")

app.on_startup(opc_update)
ui.run()
