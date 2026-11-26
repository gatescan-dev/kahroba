-- 8-bit PWM generator (VHDL-2008): output is high for `duty` of every 256 clocks.
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity pwm is
  port (
    clk  : in  std_logic;
    rst  : in  std_logic;
    duty : in  std_logic_vector(7 downto 0);
    pwm_o : out std_logic;
    wrap : out std_logic
  );
end entity;

architecture rtl of pwm is
  signal cnt     : unsigned(7 downto 0) := (others => '0');
  signal duty_q  : unsigned(7 downto 0) := (others => '0');
begin
  process (clk)
  begin
    if rising_edge(clk) then
      if rst = '1' then
        cnt    <= (others => '0');
        duty_q <= (others => '0');
      else
        cnt <= cnt + 1;
        if cnt = 255 then
          duty_q <= unsigned(duty);   -- new duty takes effect at period start
        end if;
      end if;
    end if;
  end process;

  pwm_o <= '1' when cnt < duty_q else '0';
  wrap <= '1' when cnt = 255 else '0';
end architecture;
