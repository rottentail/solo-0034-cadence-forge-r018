// Package timebase owns the composition's shared time vocabulary.
//
// Editorial decisions are half-open millisecond intervals on two aligned
// timelines: the source media clock and the cumulative output clock. The
// package deliberately does not impose one rounding on those intervals.
// Each downstream consumer re-interprets them on its own grid — ffmpeg
// filter seconds, the video frame raster, audio samples, the WebVTT clock
// — and every one of those boundary conversions lives here exactly once,
// named after the consumer whose established notation it preserves, so a
// change of notation stays a deliberate, auditable act.
package timebase

import (
	"fmt"
	"math"
)

// Interval is a half-open millisecond range [In, Out) on one timeline.
type Interval struct {
	In, Out int64
}

// Len is the interval length in milliseconds.
func (i Interval) Len() int64 { return i.Out - i.In }

// Intersect returns the overlap of two intervals on the same timeline;
// ok is false when they do not overlap, including mere contact.
func (i Interval) Intersect(o Interval) (Interval, bool) {
	in, out := max(i.In, o.In), min(i.Out, o.Out)
	if out <= in {
		return Interval{}, false
	}
	return Interval{In: in, Out: out}, true
}

// Shift moves the interval by delta milliseconds, preserving its length.
func (i Interval) Shift(delta int64) Interval {
	return Interval{In: i.In + delta, Out: i.Out + delta}
}

// Seconds renders a millisecond position for ffmpeg filter arguments as
// fixed-point seconds with three decimals, the notation the encoding
// layer has always emitted.
func Seconds(ms int64) string {
	return fmt.Sprintf("%.3f", float64(ms)/1000)
}

// Stamp renders a millisecond position as a WebVTT cue timestamp.
func Stamp(ms int64) string {
	return fmt.Sprintf("%02d:%02d:%02d.%03d", ms/3600000, (ms/60000)%60, (ms/1000)%60, ms%1000)
}

// FramesMS converts a count of video frames at fps to whole milliseconds
// by integer division, the raster reading the inspection contract uses.
func FramesMS(fps, frames int) int64 {
	return int64(frames) * 1000 / int64(fps)
}

// Milliseconds rounds a seconds measurement to the nearest millisecond,
// the convention with which probe readings enter the editorial grid.
func Milliseconds(seconds float64) int64 {
	return int64(math.Round(seconds * 1000))
}
