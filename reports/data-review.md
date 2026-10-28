# 数据复核记录

日期：2026-10-27
复核对象：`isa.json`、`memory.json`
依据：

- `docs/8051 Instruction Set.html`
- `docs/ism51.pdf`（扫描件，Table 10 周期表）
- `docs/MCS-51_Programmer’s_Guide_and_Instruction_Set.PDF`（非扫描件）
- `task.md`

## 1. 覆盖范围

| 项目 | 结果 |
|---|---|
| 有效 opcode | 255 |
| opcode 槽位 | 256（`0xA5` reserved） |
| 指令形式 | 111 |
| 8051 SFR | 21 |
| 可位寻址 SFR | 11 |
| 唯一 SFR 位地址 | 88 |
| 内存空间 | 5（program / iram / sfr / xdata / bit） |

## 2. 已做的自动核对

- HTML 的 256 个 opcode 网格与详情表逐项对齐。
- HTML 字节数、guide 定义字节数、Table 11 字节数三者一致。
- 每个指令形式的周期数与该形式下所有 opcode 的周期数一致。
- 扫描件 Table 10 中 99 个形式由 OCR 匹配，周期数与 guide 一致。
- 扫描件 OCR 无法匹配或读错的 13 个形式，已回到页面图像人工核对：
  `ADDC A,Rn`、`INC A`、`RLC A`、`MOV A,direct`、`MOV @Ri,A`、
  `AJMP addr11`、`CLR C`、`JBC bit,rel`、`MOV DPTR,#data16`、
  `RET`、`RETI`、`SETB C`、`NOP`。
- 其中 `SETB C` 的 OCR 字节数为 4，页面图像为 1，已按图像修正。
- 21 个 8051 SFR 的地址与 guide Table 1 一致；5 个 8052 专属 SFR 已排除。
- 复位值与 guide Table 2 一致；未定义位以掩码记录，未静默填 0。
- SFR 位地址、可位寻址属性、位复位值已逐项校验。

## 3. 人工对照原文后修正的问题

1. **`RL A` 的 C 标志**
   - HTML 的 Flags 列写了 `C`。
   - guide 的 flag 表和 RL 说明都明确：RL 不影响任何标志。
   - 已改为 `C: unchanged`，并在 `isa.json` 的 `notes` 中记录来源冲突。

2. **`MUL AB` / `DIV AB` 的 C 标志**
   - HTML 只写 C 受影响。
   - guide 明确 C 总是清零；已改为 `C: cleared`。

3. **指令语法规范化**
   - 修正 `INC @Ri`、`DEC @Ri`、`MOV @Ri,A`、`CJNE @Ri,...`、
     `JMP @A+DPTR`、`MOVC A,@A+DPTR`、`MOVX @DPTR,A` 等缺少空格或
     使用 PDF 内部符号的写法。
   - 原始 guide 标题保留在 `source_refs.guide.heading`。

4. **寄存器影响的补全**
   - `ANL/ORL/XRL direct,...` 现在列出 `direct` 为写目标。
   - `INC/DEC @Ri`、`MOV @Ri,...` 列出 `@Ri`。
   - `POP direct` 列出 `direct`；`PUSH direct` 不把 `direct` 误列为写目标。
   - `JBC` 列出被清位的 `bit`。
   - `MOVX @DPTR,A` / `MOVX @Ri,A` 不再把 A 误列为写目标。
   - `MOVC/JMP` 补充 A/DPTR/PC 的读取关系。

5. **端口位复位值**
   - `P0-P3` 的 SFR 复位值是 `0xFF`，因此每个端口位的复位值应为 1。
   - 已修正 `memory.json` 中端口位的 `reset`。

6. **8052 位名清理**
   - `IE.5`、`IP.5` 在 8051 上是保留位，不再命名为 `ET2` / `PT2`；
     仅在说明中注明 8052 对应位。

7. **文本瑕疵**
   - 修正 PDF 连字符断行造成的 `highand` / `lowand`。
   - 修正 `all bits set on zero` 为 `set to zero`。
   - Operation 中的 `w/a/b/e/x/kl/l/k/¶/!/I` 等 PDF 符号已转成可读符号；
     原始文本保留在 `operation_raw`。

8. **内存细节补充**
   - 增加 Timer 0 模式 3 的 TL0/TH0/T1 归属说明。
   - 增加 `POP SP` 的特殊行为说明。
   - 增加 RETI 对同优先级/低优先级中断的处理说明。

## 4. 当前状态

- `tools/build_data.py` 输出：0 issues，0 warnings。
- `tools/check_data.py` 输出：`ok: true`，0 errors。
- 111/111 指令有说明、Operation、Encoding。
- 255/255 有效 opcode 有字节数、周期数、flags 和寄存器影响。
- 21/21 8051 SFR 有地址、复位值、位定义和来源。
- 扫描件 OCR 失败项已逐项回到页面图像确认。

## 5. 明确的文件边界

- 原始文档未修改。
- `build/` 是派生文本/OCR/中间结果，不是最终数据。
- 最终数据只有 `isa.json` 和 `memory.json`。
- 本轮没有写 `hex.py`、`core.py`、`plugin.py`、`step.py`、`main.py`。
