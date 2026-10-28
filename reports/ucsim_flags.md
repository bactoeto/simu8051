# ucsim flags 差分测试报告

日期：2026-10-28

- ucsim：`/usr/bin/ucsim_51`（0.8.15）
- 模式：full
- 用例：1086
- 严格不一致：0
- 已裁决差异：35
- 错误：0

Intel MCS-51 guide 是最终裁决依据；ucsim 仅作参考实现。

## 覆盖范围

| 指令 | 用例 | 严格不一致 |
|---|---:|---:|
| ADD A,Rn | 81 | 0 |
| ADDC A,Rn | 162 | 0 |
| SUBB A,Rn | 162 | 0 |
| MUL AB | 81 | 0 |
| DIV AB | 81 | 0 |
| DA A | 32 | 0 |
| RL A | 18 | 0 |
| RR A | 18 | 0 |
| RLC A | 18 | 0 |
| RRC A | 18 | 0 |
| CLR C | 2 | 0 |
| SETB C | 2 | 0 |
| CPL C | 2 | 0 |
| ANL C,bit | 4 | 0 |
| ANL C,/bit | 4 | 0 |
| ORL C,bit | 4 | 0 |
| ORL C,/bit | 4 | 0 |
| MOV C,bit | 2 | 0 |
| CJNE A,direct,rel | 81 | 0 |
| CJNE A,#data,rel | 81 | 0 |
| CJNE Rn,#data,rel | 81 | 0 |
| CJNE @Ri,#data,rel | 81 | 0 |
| MOV PSW,#data | 12 | 0 |
| POP PSW | 12 | 0 |
| ANL PSW,#data | 6 | 0 |
| ORL PSW,#data | 6 | 0 |
| XRL PSW,#data | 6 | 0 |
| INC PSW | 10 | 0 |
| DEC PSW | 10 | 0 |
| XCH A,PSW | 5 | 0 |

值集合：`00 01 0F 10 7F 80 81 FE FF`；DA 用 `00 09 0A 0F 10 99 9A FF`。
每条用例比较 A、B、C、AC、OV、P 与 PSW 高位（F0/F1/RS0/RS1）；DIV 除零只比较 C/OV。

## 结果

全部用例的 A、B、C、AC、OV、P 与 PSW 高位一致，无严格不一致。

## 严格不一致

无。

## 已裁决差异

### PSW.0（P）：直接写 PSW 之后

- 用例数：35
- 现象：除 P 外所有位一致。ucsim 保留写入、递增或弹出得到的 PSW.0；我们按 ACC 重新计算 P。
- Guide：只规定 P 由硬件维护——“Set/cleared by hardware each instruction cycle to indicate an odd/even number of ‘1’ bits in the accumulator.”（MCS-51 Programmer’s Guide and Instruction Set, Nov 1992, Order 270249-003, p. 9 / PDF p. 12）。Guide 没有单独定义“写 PSW.0”这一动作的结果，即写入本身不是受支持的置位方式。
- ucsim 探针：`MOV A,#1; MOV PSW,#0; NOP; NOP` 后 ucsim A=0x01、P=0。两个周期后 P 仍未回到 ACC 的奇偶性，违反“each instruction cycle”。其源码只在写 ACC 时重算 P（`sim/ucsim/src/sims/s51.src/uc51.cc`，`cl_uc51_cpu::write`，ACC 分支）。
- 裁决：按 guide 的硬件维护语义，P 应等于 ACC 的奇偶性；我们强制这一点，ucsim 为偏差，不修改 `core.py`。
- 保留意见：写 PSW.0 与同周期硬件更新的先后顺序，guide 没有相位级规定；本项目不建模 S1–S6 相位，指令边界上取 P=parity(A) 是自洽选择。要绝对确认，需要在真实 8051 上做单指令实验。
- 例：`MOV PSW,#00`，A=0x01 → ucsim PSW=0x00（P=0），我们 PSW=0x01（P=1）。

| 指令 | 差异数 |
|---|---:|
| ANL PSW,#data | 4 |
| DEC PSW | 10 |
| INC PSW | 10 |
| MOV PSW,#data | 6 |
| XCH A,PSW | 3 |
| XRL PSW,#data | 2 |

### DIV AB 且 B=0

- Guide 定义 A、B 结果为未定义；两边都把 A/B 保持原值，没有可判定的差异。
- 只比较 C/OV：C=0、OV=1，全部一致。

### DA A 与 MUL/DIV 的 AC

- 用完整值集合比较 AC，未发现差异；与 `data/isa.json` 的 `AC: unchanged` 一致。

## 错误

无。

## 复现

```bash
python3 tools/compare_ucsim_flags.py            # 完整矩阵
python3 tools/compare_ucsim_flags.py --quick    # 缩减矩阵
python3 -m unittest discover -s tests
python3 tools/check_data.py
python3 tools/compare_ucsim_cycles.py
```

## 限制

- 只覆盖单指令、确定性 flags；不含定时器、中断、端口与串口。
- 操作数取自固定值集合，不是全部 256×256 组合。
- CJNE 只比较 flags，不比较分支目标。
- 结论绑定 ucsim 版本；升级 `sdcc-ucsim` 后必须重跑。
