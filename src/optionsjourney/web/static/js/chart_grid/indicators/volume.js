const UP = 'rgba(38, 166, 154, 0.5)';
const DOWN = 'rgba(239, 83, 80, 0.5)';

export class Volume {
  init(candles) {
    return {
      initialPoints: candles.map(c => ({
        time: c.time,
        value: c.volume || 0,
        color: c.close >= c.open ? UP : DOWN,
      })),
    };
  }
  update(candle) {
    return {
      point: {
        time: candle.time,
        value: candle.volume || 0,
        color: candle.close >= candle.open ? UP : DOWN,
      },
    };
  }
}
