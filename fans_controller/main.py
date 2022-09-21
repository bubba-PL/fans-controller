from dataclasses import dataclass
from typing import List, Literal

from settings import (
    NBFC_PATH,
    MAX_TEMP,
    # CRITICAL_TEMP
)
import subprocess
import json
import os
import numpy as np
import time
import sys
import fcntl
from timeit import default_timer as timer


fl = fcntl.fcntl(sys.stdin.fileno(), fcntl.F_GETFL)
fcntl.fcntl(sys.stdin.fileno(), fcntl.F_SETFL, fl | os.O_NONBLOCK)

PROBE_COMMAND = ['sudo', 'mono', f'{NBFC_PATH}/ec-probe.exe']


def get_module_location():
    return os.path.split(__file__)[0]


EC_ADDRESS = str("/sys/kernel/debug/ec/ec0/io")


def get_register_list():
    with open(EC_ADDRESS, "rb") as f:
        content = f.read()
    registers_list = content.hex('-').split('-')
    return registers_list


# def read_register(address):
#     registers_list = get_register_list()
#     return int(registers_list[address], 16)
#
#
# def write_register(address, value):
#     registers_list = get_register_list()
#     write_val = ('0' + hex(value).replace('0x', ''))[-2:]
#     registers_list[address] = write_val
#     registers_string = ' '.join(registers_list)
#     registers_bytes = bytes.fromhex(registers_string)
#     with open(EC_ADDRESS, "wb") as f:
#         f.write(registers_bytes)


class Register:
    def __init__(self, **kwargs) -> None:
        self.register: int = kwargs.get("register")

    @staticmethod
    def __serialize_value__(stdout_val: bytes):
        str_val = stdout_val.decode('utf-8')
        int_val = int(str_val.split(' ')[0])
        return int_val

    # @property
    # def value(self):
    #     ARGS = ["read",str(self.register)]
    #     result = subprocess.run(
    #         PROBE_COMMAND + ARGS, 
    #         stdout=subprocess.PIPE
    #         )
    #     value = self.__serialize_value__(result.stdout)
    #     return value

    # @property
    # def value(self):
    #     registers_list = get_register_list()
    #     return int(registers_list[self.register], 16)
    
    # def write(self, value):
    #     ARGS = ["write",str(self.register),str(value)]
    #     subprocess.run(PROBE_COMMAND + ARGS)

    # def write(self, value):
    #     registers_list = get_register_list()
    #     write_val = ('0' + hex(value).replace('0x', ''))[-2:]
    #     registers_list[self.register] = write_val
    #     registers_string = ' '.join(registers_list)
    #     registers_bytes = bytes.fromhex(registers_string)
    #     with open(EC_ADDRESS, "wb") as f:
    #         f.write(registers_bytes)

    def read(self, registers_list: List[str]):
        return int(registers_list[self.register], 16)

    def write(self, value, registers_list: List[str]):
        write_val = ('0' + hex(value).replace('0x', ''))[-2:]
        registers_list[self.register] = write_val
        return registers_list


class Mode(Register):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.manual: int = kwargs.get("manual")
        self.auto: int = kwargs.get("auto")

    def __set_auto__(self, registers_list: List[str]):
        return self.write(self.auto, registers_list)
    
    def __set_manual__(self, registers_list):
        return self.write(self.manual, registers_list)

    def set_mode(self, mode: str, registers_list: List[str]) -> List[str]:
        mode_register = {
            'auto': self.__set_auto__,
            'manual': self.__set_manual__
        }
        return mode_register[mode](registers_list)


class FanRegister(Register):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.min: int = kwargs.get("min")
        self.max: int = kwargs.get("max")


class Fan:
    RESOLUTION = 50

    def __init__(self, config_json: dict) -> None:
        self.name: str = config_json["name"]
        self.__mode: Mode = Mode(**config_json["mode"])
        self.__write: FanRegister = FanRegister(**config_json["write"])
        self.__read: FanRegister = FanRegister(**config_json["read"])
        self.__temp: FanRegister = FanRegister(
            register=config_json["temp"],
            min=0,
            max=MAX_TEMP,
        )
        self.__read_history: "list[int]" = []
        self.__temp_history: "list[int]" = []

    def map_value(self, value, range_min, range_max):
        return int(((value - range_min)/(range_max - range_min))*self.RESOLUTION)
    
    def unmap_value(self, value, range_min, range_max):
        return int(((value*(range_max-range_min))/self.RESOLUTION) + range_min)

    def represent_value(self, val):
        values = {
            'filler': '█',
            'empty': ' ',
            0: " ",
            1: "▁",
            2: "▂",
            3: "▃",
            4: "▄",
            5: "▅",
            6: "▆",
            7: "▇",
            8: "█",
        }
        values_len = len(values) - 2
        max_multiplier = int(self.RESOLUTION/values_len)
        multiplier = int(val/values_len)
        residual = val % values_len
        base = multiplier*[values["filler"]]
        out = (max_multiplier - multiplier) * [values['empty']] + [values[residual]] + base
        return out

    def serialize_history(self, history, range_min, range_max):
        mapped = [int(((val - range_min)/(range_max - range_min))*self.RESOLUTION) for val in history]
        represented = np.array([self.represent_value(val) for val in mapped])
        represented = np.transpose(represented)
        out = ""
        for i in represented:
            out += ''.join(i)
            out += '\n'
        return out

    def make_graph(self, history: 'list[int]', register: FanRegister, registers_list):
        @dataclass
        class Graphed:
            history: "list[int]"
            graph: str
        columns: int = os.get_terminal_size().columns
        # columns: int = 15
        history.append(register.read(registers_list))
        hist_len = len(history)
        if hist_len > columns:
            history = history[(hist_len - columns):]
        graph = str(self.serialize_history(history, register.min, register.max))
        return Graphed(history, graph)

    def get_summary(self, registers_list):
        out = ""

        out += self.name + '\n'
        out += f"mode: {self.get_mode(registers_list)}\n"
        graphed_read = self.make_graph(self.__read_history, self.__read, registers_list)
        self.__read_history = graphed_read.history
        speed_raw = self.__read_history[-1]
        speed_percent = int((self.map_value(speed_raw, self.__read.min, self.__read.max)/self.RESOLUTION) * 100)
        out += f'fan speed: {speed_percent}%\n'
        out += graphed_read.graph + '\n'
        
        graphed_temp = self.make_graph(self.__temp_history, self.__temp, registers_list)
        self.__temp_history = graphed_temp.history
        out += f'temperature: {self.__temp_history[-1]}°C\n'
        out += graphed_temp.graph + '\n'

        return out
    
    def set_speed(self, speed: float, registers_list):
        registers_list = self.__mode.set_mode('manual', registers_list)
        speed = int(speed*self.RESOLUTION)
        speed = self.unmap_value(speed, self.__write.min, self.__write.max)
        registers_list = self.__write.write(speed, registers_list)
        return registers_list

    def set_mode(self, mode: Literal["mode", "auto"], registers_list):
        registers_list = self.__mode.set_mode(mode, registers_list)
        return registers_list

    def get_mode(self, registers_list):
        if self.__mode.manual == self.__mode.read(registers_list):
            return "manual"
        else:
            return "auto"


class ViewController:

    @staticmethod
    def __load_configs__():
        file_path: str = os.path.join(get_module_location(), "config.json")
        with open(file_path, "r") as f:
            config: dict = json.load(f)
        return config
    
    @staticmethod
    def __make_fans__(fan_configs: "list[dict]"):
        fans: "dict[str,Fan]" = {}
        for config in fan_configs:
            fan = Fan(config)
            fans[fan.name] = fan
        return fans

    @staticmethod
    def __enable_ec_write_access():
        subprocess.run(["sudo", "modprobe", "-r", "ec_sys"])
        subprocess.run(["sudo", "modprobe", "ec_sys", "write_support=1"])

    @staticmethod
    def disable_write_support():
        subprocess.run(["sudo", "modprobe", "-r", "ec_sys"])

    def set_fans_to_auto(self):
        for fan in self.fans.values():
            self.registers_list = fan.set_mode("auto", self.registers_list)

    def __init__(self) -> None:
        self.__enable_ec_write_access()
        self.registers_list = get_register_list()
        config = self.__load_configs__()
        fan_configs = config.get("fans", [])
        self.cool_boost = Register(**config.get('cool_boost', {}))
        self.registers_list = self.cool_boost.write(1, self.registers_list)
        self.fans = self.__make_fans__(fan_configs)
        self.set_fans_to_auto()
        self.view = ""
        self.command_register = {
            'set': self.set_fan_speed,
            'auto': self.set_fan_mode,
            'cool_boost': self.set_cool_boost,
            'help': self.help,
            'back': lambda: 'v',
            'wait': self.set_wait
        }
        self.wait = 5

    def set_wait(self, how_long):
        self.wait = int(how_long)
        return 'v'

    def get_cool_boost_view(self):
        if self.cool_boost.read(self.registers_list):
            return 'ON'
        else:
            return 'OFF'

    def update_view(self):
        self.view = ""
        self.view += f"cool boost: {self.get_cool_boost_view()}\n"
        for fan in self.fans.values():
            self.view += fan.get_summary(self.registers_list)

    def draw_view(self):
        os.system('clear')
        print(self.view)
    
    def set_fan_speed(self, fan_name: str, speed: float):
        speed = float(speed)
        self.registers_list = self.fans[fan_name].set_speed(speed, self.registers_list)
        return 'v'

    def set_fan_mode(self, fan_name: str):
        self.registers_list = self.fans[fan_name].set_mode("auto", self.registers_list)
        return 'v'

    @staticmethod
    def get_input():
        print('what dou you want to do?')
        while True:
            try:
                cmd = sys.stdin.read().strip()
                return cmd
            except (IOError, TypeError):
                time.sleep(1)

    def set_cool_boost(self, on):
        if on in ["False", '0']:
            on = 0
        on = int(bool(on))
        self.registers_list = self.cool_boost.write(on, self.registers_list)
        return 'v'

    def help(self):
        print(list(self.command_register.keys()))
        return 'c'

    def command(self):
        command_input = self.get_input().split(' ')
        command = command_input[0]
        args = command_input[1:]
        cmd = self.command_register[command](*args)
        return cmd

    def update_registers_list(self):
        self.registers_list = get_register_list()

    # def update_registers_list_with_fan_changes(self):
    #     changed_registers_lists = [self.registers_list] + [fan.get_registers_list() for fan in self.fans.values()]
    #     updated_registers_list = self.previous_registers_list
    #     for registers_list in changed_registers_lists:
    #         for i, _ in enumerate(registers_list):
    #             if self.previous_registers_list[1] != registers_list[i]:
    #                 updated_registers_list[i] = registers_list[i]
    #     self.registers_list = updated_registers_list

    def write_register_changes(self):
        registers_string = ' '.join(self.registers_list)
        registers_bytes = bytes.fromhex(registers_string)
        with open(EC_ADDRESS, "wb") as f:
            f.write(registers_bytes)


def update_view(view: ViewController):
    while True:
        start = timer()
        view.update_registers_list()
        view.update_view()
        view.draw_view()
        update_time = timer()-start
        print(f"updating view took: {round(update_time, 2)}s")
        time.sleep(max([view.wait - update_time, 0]))
        try:
            cmd = sys.stdin.read().strip()
            os.system("clear")
            return cmd
        except (IOError, TypeError):
            pass


def command_view(view: ViewController):
    view.update_registers_list()
    cmd = view.command()
    # view.update_registers_list_with_fan_changes()
    view.write_register_changes()
    return cmd


COMMANDS_REGISTER = {
    "v": update_view,
    "c": command_view,
    "q": lambda view: "q"
}


def main():
    view = ViewController()
    view.write_register_changes()
    cmd = "v"
    try:
        while cmd != 'q':
            try:
                cmd = COMMANDS_REGISTER[cmd](view)
            except KeyError:
                cmd = 'v'

    except KeyboardInterrupt:
        view.set_fans_to_auto()
        view.disable_write_support()
    

if __name__ == "__main__":
    main()
