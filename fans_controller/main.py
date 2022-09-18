from dataclasses import dataclass
from settings import (
    NBFC_PATH,
    MAX_TEMP,
    CRITICAL_TEMP
)
import subprocess
import json
import os
import numpy as np
import time
import sys
import fcntl


fl = fcntl.fcntl(sys.stdin.fileno(), fcntl.F_GETFL)
fcntl.fcntl(sys.stdin.fileno(), fcntl.F_SETFL, fl | os.O_NONBLOCK)

PROBE_COMMAND = ['sudo','mono',f'{NBFC_PATH}/ec-probe.exe']


def get_module_location():
    return os.path.split(__file__)[0]


config_json =        {
            "name": "CPU",
            "mode": {
                "register": 147,
                "manual": 20,
                "auto": 4
            },
            "write": {
                "register": 148,
                "min": 255,
                "max": 0
            },
            "read": {
                "register": 149,
                "min": 255,
                "max": 85
            },
            "temp": 168
        }


class Register:
    def __init__(self, **kwargs) -> None:
        self.register: int = kwargs.get("register")

    @staticmethod
    def __serialize_value__(stdout_val: bytes):
        str_val = stdout_val.decode('utf-8')
        int_val = int(str_val.split(' ')[0])
        return int_val

    @property
    def value(self):
        ARGS = ["read",str(self.register)]
        result = subprocess.run(
            PROBE_COMMAND + ARGS, 
            stdout=subprocess.PIPE
            )
        value = self.__serialize_value__(result.stdout)
        return value
    
    def write(self, value):
        ARGS = ["write",str(self.register),str(value)]
        subprocess.run(PROBE_COMMAND + ARGS)


class Mode(Register):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.manual: int = kwargs.get("manual")
        self.auto: int = kwargs.get("auto")

    def __set_auto__(self):
        self.write(self.auto)
    
    def __set_manual__(self):
        self.write(self.manual)

    def set_mode(self, mode: str):
        mode_register = {
            'auto': self.__set_auto__,
            'manual': self.__set_manual__
        }
        mode_register[mode]()



class FanRegister(Register):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.min: int = kwargs.get("min")
        self.max: int = kwargs.get("max")


class Fan:
    RESOLUTION = 50
    def __init__(self, config_json: dict) -> None:
        self.name: str = config_json["name"]
        self.mode: Mode = Mode(**config_json["mode"])
        self.write: FanRegister = FanRegister(**config_json["write"])
        self.read: FanRegister = FanRegister(**config_json["read"])
        self.temp: FanRegister = FanRegister(
            register=config_json["temp"],
            min=0,
            max=MAX_TEMP,
        )
        self.read_history: "list[int]" = []
        self.temp_history: "list[int]" = []

    def map_value(self, value, min, max):
        return int(((value - min)/(max - min))*self.RESOLUTION)
    
    def unmap_value(self, value, min, max):
        return int(((value*(max-min))/self.RESOLUTION) + min)

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

    def serialize_history(self, history, min, max):
        mapped = [int(((val - min)/(max - min))*self.RESOLUTION) for val in history]
        represented = np.array([self.represent_value(val) for val in mapped])
        represented = np.transpose(represented)
        out = ""
        for i in represented:
            out += ''.join(i)
            out+='\n'
        return out

    def make_graph(self, history: 'list[int]', register: Register):
        @dataclass
        class Grpahed:
            history: "list[int]"
            graph: str
        columns: int = os.get_terminal_size().columns
        history.append(register.value)
        hist_len = len(history)
        if hist_len > columns:
            history = history[(hist_len - columns):]
        graph = str(self.serialize_history(history, register.min, register.max))
        return Grpahed(history, graph)


    def get_summary(self):
        out = ""

        out += self.name + '\n'
        out += f"mode: {self.get_mode()}\n"
        graphed_read = self.make_graph(self.read_history, self.read)
        self.read_history = graphed_read.history
        speed_raw = self.read_history[-1]
        speed_percent = int((self.map_value(speed_raw, self.read.min, self.read.max)/self.RESOLUTION) * 100)
        out += f'fan speed: {speed_percent}%\n'
        out += graphed_read.graph + '\n'
        
        graphed_temp = self.make_graph(self.temp_history, self.temp)
        self.temp_history = graphed_temp.history
        out += f'temperature: {self.temp_history[-1]}°C\n'
        out += graphed_temp.graph + '\n'

        return out
    
    def set_speed(self, speed: float):
        self.mode.set_mode('manual')
        speed = int(speed*self.RESOLUTION)
        speed = self.unmap_value(speed, self.write.min, self.write.max)
        self.write.write(speed)
    
    def get_mode(self):
        if self.mode.manual == self.mode.value:
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
            fans[fan.name]=fan
        return fans
    
    def set_fans_to_manual(self):
        for fan in self.fans.values():
            fan.mode.set_mode("auto")

    def __init__(self) -> None:
        config = self.__load_configs__()
        fan_configs = fan_configs = config.get("fans", [])
        self.cool_boost = Register(**config.get('cool_boost', {}))
        self.cool_boost.write(1)
        self.fans = self.__make_fans__(fan_configs)
        self.set_fans_to_manual()
        self.view = ""
        self.command_register = {
            'set': self.set_fan_speed,
            'auto': self.set_fan_mode,
            'cool_boost': self.set_cool_boost,
            'help': self.help,
            'back': lambda : 'v'
        }
    
    def get_cool_boost_view(self):
        if self.cool_boost.value:
            return 'ON'
        else:
            return 'OFF'

    def update_view(self):
        self.view = ""
        self.view += f"cool boost: {self.get_cool_boost_view()}\n"
        for fan in self.fans.values():
            self.view += fan.get_summary()

    def draw_view(self):
        os.system('clear')
        print(self.view)
    
    def set_fan_speed(self, fan_name: str, speed: float):
        speed = float(speed)
        self.fans[fan_name].set_speed(speed)
        return 'v'

    def set_fan_mode(self, fan_name: str):
        self.fans[fan_name].mode.set_mode("auto")
        return 'v'

    def get_input(self):
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
        self.cool_boost.write(on)
        return 'v'

    def help(self):
        print(list(self.command_register.keys()))
        return 'c'

    def command(self):

        # os.system('clear')
        command_input = self.get_input().split(' ')
        command = command_input[0]
        args = command_input[1:]
        cmd = self.command_register[command](*args)
        return cmd


def update_view(view: ViewController):
    while(True):
        view.update_view()
        view.draw_view()
        try:
            cmd = sys.stdin.read().strip()
            os.system("clear")
            return cmd
        except (IOError, TypeError):
            pass



def command_view(view: ViewController):
    cmd = view.command()
    return cmd


COMMANDS_REGISTER = {
    "v": update_view,
    "c": command_view,
    "q": lambda view: "q"
}


def main():
    view = ViewController()
    cmd = "v"
    try:
        while cmd != 'q':
            try:
                cmd = COMMANDS_REGISTER[cmd](view)
            except KeyError:
                cmd = 'v'
            
    except KeyboardInterrupt:
        view.set_fans_to_manual()
    

if __name__ == "__main__":
    main()
