"""cocotb testbench for counter: checks counting, enable, wrap and tc."""
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, RisingEdge


async def reset(dut):
    dut.rst.value = 1
    dut.en.value = 0
    await ClockCycles(dut.clk, 3)
    dut.rst.value = 0
    await RisingEdge(dut.clk)


@cocotb.test()
async def counts_and_wraps(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await reset(dut)
    assert dut.count.value == 0
    dut.en.value = 1
    seen_tc = 0
    for i in range(1, 300):
        await RisingEdge(dut.clk)
        await FallingEdge(dut.clk)
        assert dut.count.value == i % 256, f"cycle {i}: count={int(dut.count.value)}"
        if int(dut.tc.value):
            seen_tc += 1
            assert int(dut.count.value) == 255
    assert seen_tc == 1, f"tc pulses: {seen_tc}"


@cocotb.test()
async def enable_holds(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await reset(dut)
    dut.en.value = 1
    await ClockCycles(dut.clk, 5)
    dut.en.value = 0
    await FallingEdge(dut.clk)
    held = int(dut.count.value)
    await ClockCycles(dut.clk, 10)
    assert int(dut.count.value) == held
