/**
 * Преобразование перспективы по четырём точкам — то же, что считает сервис, только в браузере.
 *
 * Нужно для проверки привязки: контур помещения с плана накладывается обратно на кадр. Если
 * четыре точки отмечены верно, контур ложится на стены в кадре; если нет — это видно сразу, до
 * всякого расчёта. Считать на сервере поздно: человек уже отправил запись и ждёт.
 */

export type Point = [number, number];
/** Матрица 3×3 по строкам. */
export type Matrix = number[];

/** Решение системы методом Гаусса с выбором главного элемента. null — система вырождена. */
function solve(rows: number[][], values: number[]): number[] | null {
  const size = values.length;
  const matrix = rows.map((row, index) => [...row, values[index]]);
  for (let column = 0; column < size; column += 1) {
    let pivot = column;
    for (let row = column + 1; row < size; row += 1) {
      if (Math.abs(matrix[row][column]) > Math.abs(matrix[pivot][column])) pivot = row;
    }
    if (Math.abs(matrix[pivot][column]) < 1e-9) return null;
    [matrix[column], matrix[pivot]] = [matrix[pivot], matrix[column]];
    for (let row = 0; row < size; row += 1) {
      if (row === column) continue;
      const factor = matrix[row][column] / matrix[column][column];
      if (!factor) continue;
      for (let k = column; k <= size; k += 1) matrix[row][k] -= factor * matrix[column][k];
    }
  }
  return matrix.map((row, index) => row[size] / row[index]);
}

/** Матрица, переводящая четыре точки source в четыре точки target. */
export function homography(source: Point[], target: Point[]): Matrix | null {
  if (source.length !== 4 || target.length !== 4) return null;
  const rows: number[][] = [];
  const values: number[] = [];
  for (let index = 0; index < 4; index += 1) {
    const [x, y] = source[index];
    const [u, v] = target[index];
    rows.push([x, y, 1, 0, 0, 0, -u * x, -u * y]);
    values.push(u);
    rows.push([0, 0, 0, x, y, 1, -v * x, -v * y]);
    values.push(v);
  }
  const solution = solve(rows, values);
  return solution ? [...solution, 1] : null;
}

export function project(matrix: Matrix, [x, y]: Point): Point {
  const w = matrix[6] * x + matrix[7] * y + matrix[8];
  const safe = Math.abs(w) < 1e-9 ? 1e-9 : w;
  return [(matrix[0] * x + matrix[1] * y + matrix[2]) / safe, (matrix[3] * x + matrix[4] * y + matrix[5]) / safe];
}
