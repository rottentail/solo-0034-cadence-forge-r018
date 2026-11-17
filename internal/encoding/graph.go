package encoding

import (
	"cadenceforge/internal/domain"
	"cadenceforge/internal/media"
	"cadenceforge/internal/timebase"
	"fmt"
	"strings"
)

// segmentFilters renders one placement as a trim-and-conform filter chain
// plus its concat join label. The frozen editorial intervals are handed to
// ffmpeg in its seconds notation; quantization to output frames and audio
// samples is left to the media pipeline and checked by inspection, not
// pre-computed here.
func segmentFilters(i int, p domain.Placement, r domain.Recipe, hasAudio bool) ([]string, string) {
	audio := fmt.Sprintf("[%d:a:0]atrim=start=%s:end=%s,asetpts=PTS-STARTPTS,aresample=%d,aformat=sample_fmts=fltp:channel_layouts=stereo,volume=%.3fdB[a%d]", i, timebase.Seconds(p.SourceInMS), timebase.Seconds(p.SourceOutMS), r.SampleRate, p.GainDB, i)
	if !hasAudio {
		audio = fmt.Sprintf("anullsrc=r=%d:cl=stereo,atrim=duration=%s,asetpts=PTS-STARTPTS[a%d]", r.SampleRate, timebase.Seconds(p.OutputOutMS-p.OutputInMS), i)
	}
	if r.Format != "mp4" {
		return []string{audio}, fmt.Sprintf("[a%d]", i)
	}
	video := fmt.Sprintf("[%d:v:0]trim=start=%s:end=%s,setpts=PTS-STARTPTS,scale=%d:%d:force_original_aspect_ratio=decrease,pad=%d:%d:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=%d,format=yuv420p[v%d]", i, timebase.Seconds(p.SourceInMS), timebase.Seconds(p.SourceOutMS), r.Width, r.Height, r.Width, r.Height, r.FPS, i)
	return []string{audio, video}, fmt.Sprintf("[v%d][a%d]", i, i)
}
func Arguments(render domain.Render, paths map[string]string, output string) ([]string, error) {
	r := render.Recipe
	args := []string{"-nostdin", "-y", "-hide_banner", "-loglevel", "error", "-filter_complex_threads", "1"}
	filters := []string{}
	joins := []string{}
	for i, p := range render.Plan.Placements {
		path, ok := paths[p.SourceID]
		if !ok {
			return nil, fmt.Errorf("source object missing")
		}
		args = append(args, "-protocol_whitelist", "file,pipe", "-format_whitelist", media.Formats, "-i", path)
		chain, join := segmentFilters(i, p, r, render.Sources[p.SourceID].Media.Audio)
		filters = append(filters, chain...)
		joins = append(joins, join)
	}
	if len(joins) == 0 {
		return nil, fmt.Errorf("empty render")
	}
	if r.Format == "mp4" {
		filters = append(filters, strings.Join(joins, "")+fmt.Sprintf("concat=n=%d:v=1:a=1[vout][acat]", len(joins)))
	} else {
		filters = append(filters, strings.Join(joins, "")+fmt.Sprintf("concat=n=%d:v=0:a=1[acat]", len(joins)))
	}
	if r.Normalize {
		filters = append(filters, fmt.Sprintf("[acat]loudnorm=I=%.2f:TP=-1.5:LRA=11,aresample=%d[aout]", r.TargetLUFS, r.SampleRate))
	} else {
		filters = append(filters, "[acat]anull[aout]")
	}
	args = append(args, "-filter_complex", strings.Join(filters, ";"))
	if r.Format == "mp4" {
		args = append(args, "-map", "[vout]", "-map", "[aout]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-threads", "1", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart")
	} else {
		args = append(args, "-map", "[aout]", "-c:a", "pcm_s16le")
	}
	return append(args, "-ar", fmt.Sprint(r.SampleRate), "-t", timebase.Seconds(render.Plan.DurationMS), "-map_metadata", "-1", output), nil
}
