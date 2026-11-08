// 기준 설계 — scripts/rtl_design_tasks.py 의 시험기가 맞는지 확인하는 데만 쓴다(/rtl 의 모델에게는 보이지 않는다).
module gpu_fill(input wire clk, input wire rst, input wire start,
                input wire [3:0] x0, input wire [3:0] y0, input wire [4:0] w, input wire [4:0] h,
                input wire [7:0] color,
                output reg busy, output reg we, output reg [7:0] waddr, output reg [7:0] wdata);
  reg [4:0] cx, cy, xs, xe, ye;
  wire [5:0] xsum = {2'b00, x0} + {1'b0, w};
  wire [5:0] ysum = {2'b00, y0} + {1'b0, h};
  wire [4:0] xlim = (xsum > 6'd16) ? 5'd16 : xsum[4:0];
  wire [4:0] ylim = (ysum > 6'd16) ? 5'd16 : ysum[4:0];
  wire [4:0] nx = cx + 5'd1;
  wire [4:0] ny = cy + 5'd1;
  always @(posedge clk) begin
    if (rst) begin
      busy <= 1'b0; we <= 1'b0; waddr <= 8'd0; wdata <= 8'd0;
      cx <= 5'd0; cy <= 5'd0; xs <= 5'd0; xe <= 5'd0; ye <= 5'd0;
    end else if (!busy) begin
      if (start && w != 5'd0 && h != 5'd0) begin
        busy <= 1'b1; we <= 1'b1; waddr <= {y0, x0}; wdata <= color;
        cx <= {1'b0, x0}; cy <= {1'b0, y0}; xs <= {1'b0, x0}; xe <= xlim; ye <= ylim;
      end else begin
        we <= 1'b0;
      end
    end else if (nx != xe) begin
      cx <= nx; waddr <= {cy[3:0], nx[3:0]};
    end else if (ny != ye) begin
      cx <= xs; cy <= ny; waddr <= {ny[3:0], xs[3:0]};
    end else begin
      busy <= 1'b0; we <= 1'b0;
    end
  end
endmodule
