"""cocotb testbench for mac8: random stream against a Python reference model."""
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge


def wrap20(x):
    x &= (1 << 20) - 1
    return x - (1 << 20) if x >> 19 else x


@cocotb.test()
async def random_stream(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.rst.value = 1
    dut.valid.value = 0
    dut.clear.value = 0
    dut.a.value = 0
    dut.b.value = 0
    await ClockCycles(dut.clk, 3)
    dut.rst.value = 0
    rng = random.Random(7)
    acc = 0
    pipe = []  # (valid, clear, product) entering the multiplier stage
    checked = 0
    for cyc in range(2000):
        await FallingEdge(dut.clk)
        v, c = rng.random() < 0.8, rng.random() < 0.03
        a, b = rng.randint(-128, 127), rng.randint(-128, 127)
        dut.valid.value, dut.clear.value = int(v), int(c)
        dut.a.value, dut.b.value = a & 0xFF, b & 0xFF
        pipe.append((v, c, a * b))
        if len(pipe) >= 3:
            pv, pc, pp = pipe[-3]
            if pc:
                acc = pp if pv else 0
            elif pv:
                acc = wrap20(acc + pp)
            if int(dut.out_valid.value) != int(pv):
                raise AssertionError(f"cycle {cyc}: out_valid {dut.out_valid.value} != {int(pv)}")
            got = dut.acc.value.to_signed()
            assert got == acc, f"cycle {cyc}: acc {got} != model {acc}"
            checked += 1
    dut._log.info(f"checked {checked} cycles against the model")
