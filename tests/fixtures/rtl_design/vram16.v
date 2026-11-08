// 기준 설계 — scripts/rtl_design_tasks.py 의 시험기가 맞는지 확인하는 데만 쓴다(/rtl 의 모델에게는 보이지 않는다).
// 리셋은 '쓴 적 있음' 비트 256개만 한 번에 지운다 — 메모리 자체는 리셋하지 않아 BRAM 에 맞고, Verilator -Wall 경고가 없다
// (256칸을 for 반복의 <= 로 지우면 Verilator 5.020 은 BLKLOOPINIT 오류를 낸다, 2026-11-08 확인).
module vram16(input wire clk, input wire rst,
              input wire we, input wire [7:0] waddr, input wire [7:0] wdata,
              output reg [3:0] x, output reg [3:0] y, output reg [7:0] pixel, output reg frame_start);
  reg [7:0] mem [0:255];
  reg [255:0] written;
  wire [3:0] nx = (x == 4'd15) ? 4'd0 : x + 4'd1;
  wire [3:0] ny = (x == 4'd15) ? y + 4'd1 : y;
  wire [7:0] naddr = {ny, nx};
  always @(posedge clk) begin
    if (rst) begin
      written <= 256'd0;
      x <= 4'd0; y <= 4'd0; pixel <= 8'd0; frame_start <= 1'b1;
    end else begin
      x <= nx; y <= ny;
      pixel <= (we && waddr == naddr) ? wdata : (written[naddr] ? mem[naddr] : 8'd0);
      frame_start <= (nx == 4'd0) && (ny == 4'd0);
      if (we) begin
        mem[waddr] <= wdata;
        written[waddr] <= 1'b1;
      end
    end
  end
endmodule
