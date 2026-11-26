"""cocotb testbench for the VHDL PWM: measures the duty cycle per period."""
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, RisingEdge


@cocotb.test()
async def duty_cycles(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.rst.value = 1
    dut.duty.value = 0
    await ClockCycles(dut.clk, 3)
    dut.rst.value = 0
    for duty in (0, 1, 64, 128, 200, 255):
        dut.duty.value = duty
        # wait for the period boundary so the new duty is latched
        while True:
            await FallingEdge(dut.clk)
            if int(dut.wrap.value):
                break
        await RisingEdge(dut.clk)
        high = 0
        for _ in range(256):
            await FallingEdge(dut.clk)
            high += int(dut.pwm_o.value)
        dut._log.info(f"duty={duty}: high {high}/256")
        assert high == duty, f"duty {duty}: measured {high}"
