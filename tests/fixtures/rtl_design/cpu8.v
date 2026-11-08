// 기준 설계 — scripts/rtl_design_tasks.py 의 시험기가 맞는지 확인하는 데만 쓴다(/rtl 의 모델에게는 보이지 않는다).
module cpu8(input wire clk, input wire rst,
            output wire [7:0] pc, input wire [15:0] instr,
            output wire [31:0] regs, output wire zero, output wire halted);
  reg [7:0] pc_q;
  reg [7:0] r [0:3];
  reg [7:0] mem [0:15];
  reg z_q, halt_q;
  wire [3:0] op = instr[15:12];
  wire [1:0] rd = instr[11:10];
  wire [1:0] rs = instr[9:8];
  wire [7:0] imm = instr[7:0];
  wire [7:0] a = r[rd];
  wire [7:0] b = (op == 4'h8) ? imm : r[rs];
  reg [7:0] alu;
  integer i;
  always @(*) begin
    case (op)
      4'h3, 4'h8: alu = a + b;
      4'h4: alu = a - b;
      4'h5: alu = a & b;
      4'h6: alu = a | b;
      default: alu = a ^ b;
    endcase
  end
  always @(posedge clk) begin
    if (rst) begin
      pc_q <= 8'd0; z_q <= 1'b0; halt_q <= 1'b0;
      for (i = 0; i < 4; i = i + 1) r[i] <= 8'd0;
      for (i = 0; i < 16; i = i + 1) mem[i] <= 8'd0;
    end else if (!halt_q) begin
      pc_q <= pc_q + 8'd1;
      case (op)
        4'h1: r[rd] <= imm;
        4'h2: r[rd] <= r[rs];
        4'h3, 4'h4, 4'h5, 4'h6, 4'h7, 4'h8: begin r[rd] <= alu; z_q <= (alu == 8'd0); end
        4'h9: r[rd] <= mem[imm[3:0]];
        4'hA: mem[imm[3:0]] <= r[rs];
        4'hB: pc_q <= imm;
        4'hC: if (z_q) pc_q <= imm;
        4'hD: if (!z_q) pc_q <= imm;
        4'hF: begin halt_q <= 1'b1; pc_q <= pc_q; end
        default: ;
      endcase
    end
  end
  assign pc = pc_q;
  assign regs = {r[3], r[2], r[1], r[0]};
  assign zero = z_q;
  assign halted = halt_q;
endmodule
