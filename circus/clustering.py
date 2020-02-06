from circus.shared.utils import *
import circus.shared.files as io
import circus.shared.algorithms as algo
from circus.shared import plot
import warnings
with warnings.catch_warnings():
    warnings.filterwarnings("ignore", category=FutureWarning)
    import h5py
from circus.shared.probes import get_nodes_and_edges
from circus.shared.files import get_dead_times
from circus.shared.messages import print_and_log, init_logging
from circus.shared.utils import get_parallel_hdf5_flag
from circus.shared.mpi import detect_memory


def main(params, nb_cpu, nb_gpu, use_gpu):

    numpy.random.seed(520)
    parallel_hdf5 = get_parallel_hdf5_flag(params)
    _ = init_logging(params.logfile)
    logger = logging.getLogger('circus.clustering')
    #################################################################
    data_file = params.data_file
    N_e = params.getint('data', 'N_e')
    N_total = params.nb_channels
    N_t = params.getint('detection', 'N_t')
    dist_peaks = params.getint('detection', 'dist_peaks')
    template_shift = params.getint('detection', 'template_shift')
    file_out_suff = params.get('data', 'file_out_suff')
    sign_peaks = params.get('detection', 'peaks')
    alignment = params.getboolean('detection', 'alignment')
    isolation = params.getboolean('detection', 'isolation')
    over_factor = float(params.getint('detection', 'oversampling_factor'))
    matched_filter = params.getboolean('detection', 'matched-filter')
    _ = params.getfloat('detection', 'spike_thresh')
    spike_width = params.getfloat('detection', 'spike_width')
    noise_thresh = params.getfloat('clustering', 'noise_thr')
    if params.getboolean('data', 'global_tmp'):
        tmp_path_loc = os.path.join(os.path.abspath(params.get('data', 'file_out_suff')), 'tmp')
    else:
        tmp_path_loc = tempfile.gettempdir()

    plot_path = os.path.join(params.get('data', 'file_out_suff'), 'plots')
    do_temporal_whitening = params.getboolean('whitening', 'temporal')
    do_spatial_whitening = params.getboolean('whitening', 'spatial')
    safety_time = params.getint('clustering', 'safety_time')
    safety_space = params.getboolean('clustering', 'safety_space')
    comp_templates = params.getboolean('clustering', 'compress')
    dispersion = params.get('clustering', 'dispersion').replace('(', '').replace(')', '').split(',')
    dispersion = map(float, dispersion)
    nodes, edges = get_nodes_and_edges(params)
    chunk_size = detect_memory(params)
    max_elts_elec = params.getint('clustering', 'max_elts')
    two_components = params.getboolean('clustering', 'two_components')
    if sign_peaks == 'both':
        max_elts_elec *= 2
    nb_elts = int(params.getfloat('clustering', 'nb_elts') * N_e * max_elts_elec)
    nb_repeats = params.getint('clustering', 'nb_repeats')
    make_plots = params.get('clustering', 'make_plots')
    debug_plots = params.get('clustering', 'debug_plots')
    merging_param = params.getfloat('clustering', 'merging_param')
    merging_method = params.get('clustering', 'merging_method')
    remove_mixture = params.getboolean('clustering', 'remove_mixture')
    extraction = params.get('clustering', 'extraction')
    smart_search = params.getboolean('clustering', 'smart_search')
    n_abs_min = params.getint('clustering', 'n_abs_min')
    sensitivity = params.getfloat('clustering', 'sensitivity')
    hdf5_compress = params.getboolean('data', 'hdf5_compress')
    blosc_compress = params.getboolean('data', 'blosc_compress')
    test_clusters = params.getboolean('clustering', 'test_clusters')
    sparsify = params.getfloat('clustering', 'sparsify')
    debug = params.getboolean('clustering', 'debug')
    tmp_limits = params.get('fitting', 'amp_limits').replace('(', '').replace(')', '').split(',')
    _ = map(float, tmp_limits)
    elt_count = 0
    m_ratio = params.getfloat('clustering', 'm_ratio')
    sub_output_dim = params.getint('clustering', 'sub_dim')
    inv_nodes = numpy.zeros(N_total, dtype=numpy.int32)
    inv_nodes[nodes] = numpy.arange(len(nodes))
    to_write = ['clusters_', 'times_', 'data_', 'peaks_']
    if debug:
        to_write += ['rho_', 'delta_']
    ignore_dead_times = params.getboolean('triggers', 'ignore_times')
    jitter_range = params.getint('detection', 'jitter_range')
    template_shift_2 = template_shift + jitter_range
    nb_ss_bins = params.getint('clustering', 'nb_ss_bins')
    use_hanning = params.getboolean('detection', 'hanning')
    use_savgol = params.getboolean('clustering', 'savgol')
    rejection_threshold = params.getfloat('detection', 'rejection_threshold')
    smoothing_factor = params.getfloat('detection', 'smoothing_factor')
    noise_window = params.getint('detection', 'noise_time')
    data_file.open()
    #################################################################

    if rejection_threshold > 0:
        reject_noise = True
    else:
        reject_noise = False

    if sign_peaks == 'negative':
        search_peaks = ['neg']
    elif sign_peaks == 'positive':
        search_peaks = ['pos']
    elif sign_peaks == 'both':
        search_peaks = ['neg', 'pos']
    else:
        raise ValueError("unexpected value: %s" % sign_peaks)

    nodes_indices = {}
    for elec in numpy.arange(N_e):
        nodes_indices[elec] = inv_nodes[edges[nodes[elec]]]

    smart_searches = {}
    for p in search_peaks:
        smart_searches[p] = numpy.ones(N_e, dtype=numpy.float32)*int(smart_search)

    basis = {}

    if use_hanning:
        hanning_filter = numpy.hanning(N_t)[:, numpy.newaxis]
    else:
        hanning_filter = None  # default assignment (for PyCharm code inspection)

    if use_savgol:
        savgol_filter = numpy.hanning(N_t)
        savgol_window = params.getint('clustering', 'savgol_window')
    else:
        savgol_filter = None  # default assignment (for PyCharm code inspection)
        savgol_window = None  # default assignment (for PyCharm code inspection)

    if alignment:
        cdata = numpy.linspace(-jitter_range, jitter_range, int(over_factor * 2 * jitter_range))
        xdata = numpy.arange(-template_shift_2, template_shift_2 + 1)
        xoff = len(cdata) / 2.0
        duration = template_shift_2
        # if sign_peaks in ['negative', 'both']:
        #     weights_neg = smoothing_factor/io.load_data(params, 'weights')
        # if sign_peaks in ['positive', 'both']:
        #     weights_pos = smoothing_factor/io.load_data(params, 'weights-pos')
        m_size = (2 * template_shift_2 + 1)
        align_factor = m_size
    else:
        cdata = None  # default assignment (for PyCharm code inspection)
        xdata = None  # default assignment (for PyCharm code inspection)
        xoff = None  # default assignment (for PyCharm code inspection)
        duration = template_shift
        align_factor = None  # default assignment (for PyCharm code inspection)

    if sign_peaks in ['negative', 'both']:
        basis['proj_neg'], basis['rec_neg'] = io.load_data(params, 'basis')
    if sign_peaks in ['positive', 'both']:
        basis['proj_pos'], basis['rec_pos'] = io.load_data(params, 'basis-pos')

    thresholds = io.load_data(params, 'thresholds')
    mads = io.load_data(params, 'mads')
    stds = io.load_data(params, 'stds')
    n_scalar = N_e * N_t
    if do_spatial_whitening:
        spatial_whitening = io.load_data(params, 'spatial_whitening')
    else:
        spatial_whitening = None  # default assignment (for PyCharm code inspection)
    if do_temporal_whitening:
        temporal_whitening = io.load_data(params, 'temporal_whitening')
    else:
        temporal_whitening = None  # default assignment (for PyCharm code inspection)

    waveform_neg = numpy.empty(0)  # default assignment (for PyCharm code inspection)
    matched_thresholds_neg = numpy.empty(0)  # default assignment (for PyCharm code inspection)
    waveform_pos = numpy.empty(0)  # default assignment (for PyCharm code inspection)
    matched_thresholds_pos = numpy.empty(0)  # default assignment (for PyCharm code inspection)
    if matched_filter:
        if sign_peaks in ['negative', 'both']:
            waveform_neg = io.load_data(params, 'waveform')[::-1]
            waveform_neg /= (numpy.abs(numpy.sum(waveform_neg)) * len(waveform_neg))
            matched_thresholds_neg = io.load_data(params, 'matched-thresholds')
        if sign_peaks in ['positive', 'both']:
            waveform_pos = io.load_data(params, 'waveform-pos')[::-1]
            waveform_pos /= (numpy.abs(numpy.sum(waveform_pos)) * len(waveform_pos))
            matched_thresholds_pos = io.load_data(params, 'matched-thresholds-pos')

    if ignore_dead_times:
        all_dead_times = get_dead_times(params)

    result = {}

    if use_gpu:
        import cudamat as cmt
        # # Need to properly handle multi GPU per MPI nodes?
        if nb_gpu > nb_cpu:
            gpu_id = int(comm.rank // nb_cpu)
        else:
            gpu_id = 0
        cmt.cuda_set_device(gpu_id)
        cmt.init()
        cmt.cuda_sync_threads()

    if test_clusters:
        injected_spikes = io.load_data(params, 'injected_spikes')
    else:
        injected_spikes = None  # default assignment

    if comm.rank == 0:
        if not os.path.exists(tmp_path_loc):
            os.makedirs(tmp_path_loc)

    comm.Barrier()

    if use_gpu and do_spatial_whitening:
        spatial_whitening = cmt.CUDAMatrix(spatial_whitening, copy_on_host=False)

    elec_positions = {}

    for i in range(N_e):
        result['loc_times_' + str(i)] = numpy.zeros(0, dtype=numpy.uint32)
        result['all_times_' + str(i)] = numpy.zeros(0, dtype=numpy.uint32)
        result['times_' + str(i)] = numpy.zeros(0, dtype=numpy.uint32)
        result['clusters_' + str(i)] = numpy.zeros(0, dtype=numpy.int32)
        result['peaks_' + str(i)] = [numpy.empty(0, dtype=numpy.uint32)]
        for p in search_peaks:
            result['pca_%s_' % p + str(i)] = None
        indices = nodes_indices[i]
        elec_positions[i] = numpy.where(indices == i)[0]

    max_elts_elec //= comm.size
    nb_elts //= comm.size
    few_elts = False
    nb_chunks, _ = data_file.analyze(chunk_size)

    if nb_chunks < comm.size:

        res = io.data_stats(params, show=False)
        chunk_size = int(res*params.rate//comm.size)
        if comm.rank == 0:
            print_and_log(["Too much cores, automatically resizing the data chunks"], 'debug', logger)

        nb_chunks, last_chunk_len = data_file.analyze(chunk_size)

    if smart_search is False:
        gpass = 1
    else:
        gpass = 0

    # # We will perform several passes to enhance the quality of the clustering

    sdata = None  # default assignment (for PyCharm code inspection)

    while gpass < (nb_repeats + 1):

        comm.Barrier()

        if gpass == 1:
            sdata = all_gather_array(smart_searches[search_peaks[0]][comm.rank::comm.size], comm, 0)

        if comm.rank == 0:
            if gpass == 0:
                print_and_log(["Searching random spikes to sample amplitudes..."], 'default', logger)
            elif gpass == 1:
                if not numpy.all(sdata > 0):
                    lines = ["Smart Search disabled on %d electrodes" % (numpy.sum(sdata == 0))]
                    print_and_log(lines, 'debug', logger)
                if numpy.any(sdata > 0):
                    if isolation:
                        lines = [
                            "Smart Search of good isolated spikes for the clustering (%d/%d)..." % (gpass, nb_repeats)
                        ]
                        print_and_log(lines, 'default', logger)
                    else:
                        lines = ["Smart Search of good spikes for the clustering (%d/%d)..." % (gpass, nb_repeats)]
                        print_and_log(lines, 'default', logger)
                else:
                    lines = [
                        "Searching random spikes for the clustering (%d/%d) (no smart search)" % (gpass, nb_repeats)
                    ]
                    print_and_log(lines, 'default', logger)
            else:
                lines = ["Searching random spikes to refine the clustering (%d/%d)..." % (gpass, nb_repeats)]
                print_and_log(lines, 'default', logger)

        for i in range(N_e):
            if gpass == 0:
                for p in search_peaks:
                    result['tmp_%s_' % p + str(i)] = [numpy.zeros(0, dtype=numpy.float32)]
                    result['nb_chunks_%s_' % p + str(i)] = 0
                    result['count_%s_' % p + str(i)] = 0

            # If not the first pass, we sync all the detected times among nodes and give all nodes the w/pca
            result['all_times_' + str(i)] = numpy.concatenate((
                result['all_times_' + str(i)],
                all_gather_array(
                    result['loc_times_' + str(i)], comm, dtype='uint32', compress=blosc_compress
                )
            ))
            result['loc_times_' + str(i)] = numpy.zeros(0, dtype=numpy.uint32)

            if gpass == 1:
                n_neighb = len(edges[nodes[i]])
                for p in search_peaks:
                    result['data_%s_' % p + str(i)] = [
                        numpy.zeros((0, basis['proj_%s' % p].shape[1] * n_neighb), dtype=numpy.float32)
                    ]
                    result['count_%s_' % p + str(i)] = 0

            if gpass == 2:
                for p in search_peaks:
                    result['pca_%s_' % p + str(i)] = \
                        comm.bcast(result['pca_%s_' % p + str(i)], root=numpy.mod(i, comm.size))

            if gpass > 1:
                for p in search_peaks:
                    result['tmp_%s_' % p + str(i)] = [
                        numpy.zeros((0, result['pca_%s_' % p + str(i)].shape[1]), dtype=numpy.float32)
                    ]
                    result['count_%s_' % p + str(i)] = 0

        # I guess this is more relevant, to take signals from all over the recordings
        numpy.random.seed(gpass)
        all_chunks = numpy.random.permutation(numpy.arange(nb_chunks, dtype=numpy.int64))

        # # This is not easy to read, but during the smart search pass, we need to loop over all chunks, and every nodes
        # # should search spikes for a subset of electrodes, to avoid too many communications.
        if gpass == 0 or not smart_search:
            nb_elecs = numpy.sum(comm.rank == numpy.mod(numpy.arange(N_e), comm.size))
            loop_max_elts_elec = params.getint('clustering', 'max_elts')
            if sign_peaks == 'both':
                loop_max_elts_elec *= 2
            loop_nb_elts = numpy.int64(params.getfloat('clustering', 'nb_elts') * nb_elecs * loop_max_elts_elec)
            to_explore = range(nb_chunks)
        elif gpass == 1:
            if elt_count < loop_nb_elts - 1:
                lines = [
                    "Node %d found not enough spikes: searching only %d spikes instead of %d"
                    % (comm.rank, elt_count, loop_nb_elts)
                ]
                print_and_log(lines, 'debug', logger)
                loop_nb_elts = elt_count
            to_explore = range(nb_chunks)
        else:
            loop_max_elts_elec = max_elts_elec
            loop_nb_elts = nb_elts
            to_explore = range(comm.rank, nb_chunks, comm.size)

        rejected = 0
        elt_count = 0
        nb_noise = 0

        if comm.rank == 0:
            to_explore = get_tqdm_progressbar(to_explore)

        comm.Barrier()
        # # Random selection of spikes

        for gcount, gidx in enumerate(to_explore):

            gidx = all_chunks[gidx]
            is_first = data_file.is_first_chunk(gidx, nb_chunks)
            is_last = data_file.is_last_chunk(gidx, nb_chunks)

            if is_last:
                padding = (-duration, 0)
            elif is_first:
                padding = (0, duration)
            else:
                padding = (-duration, duration)

            if elt_count < loop_nb_elts:
                # print "Node", comm.rank, "is analyzing chunk", gidx, "/", nb_chunks, " ..."
                local_chunk, t_offset = data_file.get_data(gidx, chunk_size, padding, nodes=nodes)
                local_shape = len(local_chunk)
                if do_spatial_whitening:
                    if use_gpu:
                        local_chunk = cmt.CUDAMatrix(local_chunk, copy_on_host=False)
                        local_chunk = local_chunk.dot(spatial_whitening).asarray()
                    else:
                        local_chunk = numpy.dot(local_chunk, spatial_whitening)
                if do_temporal_whitening:
                    local_chunk = scipy.ndimage.filters.convolve1d(
                        local_chunk, temporal_whitening, axis=0, mode='constant'
                    )

                # Extracting the peaks.
                all_peaktimes = [numpy.empty(0, dtype=numpy.uint32)]
                all_extremas = [numpy.empty(0, dtype=numpy.uint32)]

                if matched_filter:

                    if sign_peaks in ['positive', 'both']:
                        filter_chunk = scipy.ndimage.filters.convolve1d(
                            local_chunk, waveform_pos, axis=0, mode='constant'
                        )
                        for i in range(N_e):
                            peaktimes = scipy.signal.find_peaks(
                                filter_chunk[:, i], height=matched_thresholds_pos[i],
                                width=spike_width, distance=dist_peaks, wlen=N_t
                            )[0]
                            peaktimes = peaktimes.astype(numpy.uint32)
                            all_peaktimes.append(peaktimes)
                            extremas = i * numpy.ones(len(peaktimes), dtype=numpy.uint32)
                            all_extremas.append(extremas)

                    if sign_peaks in ['negative', 'both']:
                        filter_chunk = scipy.ndimage.filters.convolve1d(
                            local_chunk, waveform_neg, axis=0, mode='constant'
                        )
                        for i in range(N_e):
                            peaktimes = scipy.signal.find_peaks(
                                filter_chunk[:, i], height=matched_thresholds_neg[i],
                                width=spike_width, distance=dist_peaks, wlen=N_t
                            )[0]
                            peaktimes = peaktimes.astype(numpy.uint32)
                            all_peaktimes.append(peaktimes)
                            extremas = i * numpy.ones(len(peaktimes), dtype=numpy.uint32)
                            all_extremas.append(extremas)

                else:

                    for i in range(N_e):
                        x = local_chunk[:, i]
                        height = thresholds[i]
                        if sign_peaks == 'negative':
                            peaktimes = scipy.signal.find_peaks(
                                -x, height=height, width=spike_width, distance=dist_peaks, wlen=N_t
                            )[0]
                        elif sign_peaks == 'positive':
                            peaktimes = scipy.signal.find_peaks(
                                +x, height=height, width=spike_width, distance=dist_peaks, wlen=N_t
                            )[0]
                        elif sign_peaks == 'both':
                            peaktimes = scipy.signal.find_peaks(
                                numpy.abs(x), height=height, width=spike_width, distance=dist_peaks, wlen=N_t
                            )[0]
                        else:
                            peaktimes = numpy.empty(0, dtype=numpy.uint32)
                        peaktimes.astype(numpy.uint32)
                        all_peaktimes.append(peaktimes)
                        extremas = i * numpy.ones(len(peaktimes), dtype=numpy.uint32)
                        all_extremas.append(extremas)

                all_peaktimes = numpy.concatenate(all_peaktimes)  # i.e. concatenate once for efficiency
                all_extremas = numpy.concatenate(all_extremas)  # i.e. concatenate once for efficiency

                # print "Removing the useless borders..."
                local_borders = (duration, local_shape - duration)

                idx = (all_peaktimes >= local_borders[0]) & (all_peaktimes < local_borders[1])
                all_peaktimes = numpy.compress(idx, all_peaktimes)
                all_extremas = numpy.compress(idx, all_extremas)

                local_peaktimes = numpy.unique(all_peaktimes)
                local_offset = t_offset + padding[0]

                if ignore_dead_times:
                    dead_indices = numpy.searchsorted(all_dead_times, [t_offset, t_offset + local_shape])
                    if dead_indices[0] != dead_indices[1]:
                        is_included = numpy.in1d(local_peaktimes + t_offset, all_dead_times[dead_indices[0]:dead_indices[1]])
                        local_peaktimes = local_peaktimes[~is_included]
                        local_peaktimes = numpy.sort(local_peaktimes)

                if gpass == 0:
                    for i in range(comm.rank, N_e, comm.size):
                        for p in search_peaks:
                            if result['count_%s_' % p + str(i)] < loop_max_elts_elec:
                                result['nb_chunks_%s_' % p + str(i)] += 1

                if len(local_peaktimes) > 0:

                    diff_times = local_peaktimes[-1]-local_peaktimes[0]
                    all_times = numpy.zeros((N_e, diff_times+1), dtype=numpy.bool)
                    min_times = numpy.maximum(local_peaktimes - local_peaktimes[0] - safety_time, 0)
                    max_times = numpy.minimum(local_peaktimes - local_peaktimes[0] + safety_time + 1, diff_times)

                    n_times = len(local_peaktimes)
                    argmax_peak = numpy.random.permutation(numpy.arange(n_times))
                    all_idx = numpy.take(local_peaktimes, argmax_peak)

                    if gpass > 1:
                        for elec in range(N_e):
                            subset = (result['all_times_' + str(elec)] - local_offset).astype(numpy.int32)
                            peaks = numpy.compress((subset >= 0) & (subset < local_shape), subset)
                            inter = numpy.in1d(local_peaktimes, peaks)
                            indices = nodes_indices[elec]
                            remove = numpy.where(inter)[0]
                            for t in remove:
                                if safety_space:
                                    all_times[indices, min_times[t]:max_times[t]] = True
                                else:
                                    all_times[elec, min_times[t]:max_times[t]] = True

                    # print "Selection of the peaks with spatio-temporal masks..."
                    for midx, peak in zip(argmax_peak, all_idx):

                        if elt_count == loop_nb_elts:
                            break

                        is_isolated = True
                        to_accept = False

                        negative_peak = None  # default assignment (for PyCharm code inspection)
                        loc_peak = None  # default assignment (for PyCharm code inspection)
                        if sign_peaks == 'negative':
                            elec = numpy.argmin(local_chunk[peak])
                            negative_peak = True
                            loc_peak = 'neg'
                        elif sign_peaks == 'positive':
                            elec = numpy.argmax(local_chunk[peak])
                            negative_peak = False
                            loc_peak = 'pos'
                        elif sign_peaks == 'both':
                            if N_e == 1:
                                elec = 0
                                if local_chunk[peak] < 0:
                                    negative_peak = True
                                    loc_peak = 'neg'
                                elif local_chunk[peak] > 0:
                                    negative_peak = False
                                    loc_peak = 'pos'
                            else:
                                if numpy.abs(numpy.max(local_chunk[peak])) > numpy.abs(numpy.min(local_chunk[peak])):
                                    elec = numpy.argmax(local_chunk[peak])
                                    negative_peak = False
                                    loc_peak = 'pos'
                                else:
                                    elec = numpy.argmin(local_chunk[peak])
                                    negative_peak = True
                                    loc_peak = 'neg'
                        else:
                            raise ValueError("unexpected value %s" % sign_peaks)

                        key = '%s_%s' % (loc_peak, str(elec))

                        if (gpass > 1) or (numpy.mod(elec, comm.size) == comm.rank):

                            if result['count_%s' % key] < loop_max_elts_elec:

                                indices = nodes_indices[elec]

                                if safety_space:
                                    myslice = all_times[indices, min_times[midx]:max_times[midx]]
                                else:
                                    myslice = all_times[elec, min_times[midx]:max_times[midx]]

                                if not myslice.any():

                                    sub_mat = numpy.take(
                                        local_chunk[peak - duration:peak + duration + 1], indices, axis=1
                                    )

                                    # # test if the sample is pure Gaussian noise
                                    if reject_noise:
                                        slice_window = sub_mat[duration - noise_window: duration + noise_window]
                                        values = numpy.linalg.norm(slice_window, axis=0) / (stds[indices] * 2 * noise_window)
                                        is_noise = numpy.all(
                                            values < rejection_threshold
                                        )
                                    else:
                                        is_noise = False

                                    if not is_noise:

                                        if isolation and gpass == 1:

                                            nearby_peaks = numpy.abs(all_peaktimes - peak) < safety_time
                                            vicinity_peaks = all_peaktimes[nearby_peaks]
                                            vicinity_extremas = all_extremas[nearby_peaks]
                                            extremas = local_chunk[vicinity_peaks, vicinity_extremas]

                                            nearby = numpy.in1d(vicinity_extremas, indices)
                                            to_consider = extremas[nearby]

                                            if len(to_consider) > 0:
                                                if negative_peak:
                                                    if numpy.any(to_consider < local_chunk[peak, elec]):
                                                        is_isolated = False
                                                else:
                                                    if numpy.any(to_consider > local_chunk[peak, elec]):
                                                        is_isolated = False

                                        if is_isolated:

                                            if alignment:

                                                # if loc_peak == 'neg':
                                                #     weights = weights_neg
                                                # elif loc_peak == 'pos':
                                                #     weights = weights_pos
                                                local_factor = align_factor * ((smoothing_factor * mads[elec]) ** 2)

                                                if len(indices) == 1:
                                                    smoothed = True
                                                    try:
                                                        f = scipy.interpolate.UnivariateSpline(
                                                            xdata, sub_mat, s=local_factor, k=3
                                                        )
                                                    except Exception:
                                                        smoothed = False
                                                        f = scipy.interpolate.UnivariateSpline(xdata, sub_mat, k=3, s=0)
                                                    if negative_peak:
                                                        rmin = (numpy.argmin(f(cdata)) - xoff) / over_factor
                                                    else:
                                                        rmin = (numpy.argmax(f(cdata)) - xoff) / over_factor
                                                    if smoothed:
                                                        f = scipy.interpolate.UnivariateSpline(xdata, sub_mat, s=0, k=3)
                                                    ddata = numpy.linspace(
                                                        rmin - template_shift, rmin + template_shift, N_t
                                                    )
                                                    sub_mat = f(ddata).astype(numpy.float32).reshape(N_t, 1)
                                                else:
                                                    idx = elec_positions[elec]
                                                    ydata = numpy.arange(len(indices))
                                                    try:
                                                        f = scipy.interpolate.UnivariateSpline(
                                                            xdata, sub_mat[:, idx], s=local_factor, k=3
                                                        )
                                                    except Exception:
                                                        f = scipy.interpolate.UnivariateSpline(
                                                            xdata, sub_mat[:, idx], k=3, s=0
                                                        )
                                                    if negative_peak:
                                                        rmin = (numpy.argmin(f(cdata)) - xoff) / over_factor
                                                    else:
                                                        rmin = (numpy.argmax(f(cdata)) - xoff) / over_factor
                                                    f = scipy.interpolate.RectBivariateSpline(
                                                        xdata, ydata, sub_mat, s=0, kx=3, ky=1
                                                    )
                                                    ddata = numpy.linspace(
                                                        rmin - template_shift, rmin + template_shift, N_t
                                                    )
                                                    sub_mat = f(ddata, ydata).astype(numpy.float32)

                                            if negative_peak:
                                                max_test = \
                                                    numpy.argmin(sub_mat[template_shift]) == elec_positions[elec][0]
                                            else:
                                                max_test = \
                                                    numpy.argmax(sub_mat[template_shift]) == elec_positions[elec][0]

                                            if max_test:
                                                if gpass == 0:
                                                    to_accept = True
                                                    ext_amp = sub_mat[template_shift, elec_positions[elec]]
                                                    result['tmp_%s_' % loc_peak + str(elec)].append(ext_amp)
                                                elif gpass == 1:

                                                    if smart_searches[loc_peak][elec] > 0:

                                                        ext_amp = sub_mat[template_shift, elec_positions[elec]]
                                                        idx = numpy.searchsorted(result['bounds_%s_' % loc_peak + str(elec)], ext_amp, 'right') - 1
                                                        to_keep = result['hist_%s_' % loc_peak + str(elec)][idx] < numpy.random.rand()

                                                        if to_keep:
                                                            to_accept = True
                                                        else:
                                                            rejected += 1

                                                    else:
                                                        to_accept = True

                                                    if to_accept:

                                                        if use_hanning:
                                                            sub_mat *= hanning_filter

                                                        sub_mat = numpy.dot(basis['rec_%s' % loc_peak], sub_mat)
                                                        nx, ny = sub_mat.shape
                                                        sub_mat = sub_mat.reshape((1, nx * ny))
                                                        # result['data_%s_' % loc_peak + str(elec)] = numpy.vstack((
                                                        #     result['data_%s_' % loc_peak + str(elec)],
                                                        #     sub_mat
                                                        # ))
                                                        result['data_%s_' % loc_peak + str(elec)].append(sub_mat)

                                                else:

                                                    if use_hanning:
                                                        sub_mat *= hanning_filter

                                                    sub_mat = numpy.dot(basis['rec_%s' % loc_peak], sub_mat)
                                                    nx, ny = sub_mat.shape
                                                    sub_mat = sub_mat.reshape((1, nx * ny))
                                                    sub_mat = numpy.dot(
                                                        sub_mat, result['pca_%s_' % loc_peak + str(elec)]
                                                    )
                                                    to_accept = True
                                                    # result['tmp_%s_' % loc_peak + str(elec)] = numpy.vstack((
                                                    #     result['tmp_%s_' % loc_peak + str(elec)],
                                                    #     sub_mat
                                                    # ))
                                                    result['tmp_%s_' % loc_peak + str(elec)].append(sub_mat)

                                        if to_accept:
                                            elt_count += 1
                                            result['count_%s_' % loc_peak + str(elec)] += 1
                                            if gpass >= 1:
                                                to_add = numpy.array([peak + local_offset], dtype=numpy.uint32)
                                                result['loc_times_' + str(elec)] = numpy.concatenate((
                                                    result['loc_times_' + str(elec)],
                                                    to_add
                                                ))
                                            if gpass == 1:
                                                result['peaks_' + str(elec)].append([int(negative_peak)])
                                            if safety_space:
                                                all_times[indices, min_times[midx]:max_times[midx]] = True
                                            else:
                                                all_times[elec, min_times[midx]:max_times[midx]] = True
                                    else:
                                        nb_noise += 1
                                        # import pylab
                                        # pylab.plot(sub_mat)
                                        # pylab.show()

        for elec in range(N_e):
            for p in search_peaks:
                if gpass == 0:
                    result['tmp_%s_' % p + str(elec)] = numpy.concatenate(result['tmp_%s_' % p + str(elec)])
                elif gpass == 1:
                    result['data_%s_' % p + str(elec)] = numpy.vstack(result['data_%s_' % p + str(elec)])
                elif gpass > 1:
                    result['tmp_%s_' % p + str(elec)] = numpy.vstack(result['tmp_%s_' % p + str(elec)])

        comm.Barrier()
        sys.stderr.flush()

        lines = [
            'Node %d has collected %d spikes and rejected %d spikes' % (comm.rank, elt_count, rejected),
            'Node %d has ignored %d noisy spikes' % (comm.rank, nb_noise)
        ]
        print_and_log(lines, 'debug', logger)
        gdata = all_gather_array(numpy.array([elt_count], dtype=numpy.float32), comm, 0)
        gdata2 = gather_array(numpy.array([rejected], dtype=numpy.float32), comm, 0)
        nb_elements = numpy.int64(numpy.sum(gdata))
        nb_rejected = numpy.int64(numpy.sum(gdata2))
        nb_total = numpy.int64(nb_elts * comm.size)

        if ((smart_search and (gpass == 0)) or (not smart_search and (gpass == 1))) and nb_elements == 0:
            if comm.rank == 0:
                print_and_log(['No waveforms found! Are the data properly loaded??'], 'error', logger)
            sys.exit(0)

        if nb_elements == 0:
            gpass = nb_repeats

        if comm.rank == 0:
            if gpass != 1:
                print_and_log(["Found %d spikes over %d requested" % (nb_elements, nb_total)], 'default', logger)
                if nb_elements == 0:
                    print_and_log(["No more spikes in the recording, stop searching"], 'info', logger)
            else:
                if isolation:
                    lines = [
                        "Found %d isolated spikes over %d requested (%d rejected)"
                        % (nb_elements, nb_total, nb_rejected)
                    ]
                    print_and_log(lines, 'default', logger)
                else:
                    lines = [
                        "Found %d spikes over %d requested (%d rejected)"
                        % (nb_elements, nb_total, nb_rejected)
                    ]
                    print_and_log(lines, 'default', logger)
                if nb_elements < 0.2 * nb_total:
                    few_elts = True

        # CLUSTERING: once we have been through enough chunks (we don't need all of them),
        # we run a clustering for each electrode.
        # print "Clustering the data..."
        local_nb_clusters = 0
        local_hits = 0
        local_mergings = 0
        cluster_results = {}
        for p in search_peaks:
            cluster_results[p] = {}

        if gpass > 1:
            for ielec in range(N_e):
                for p in search_peaks:
                    result['tmp_%s_' % p + str(ielec)] = gather_array(
                        result['tmp_%s_' % p + str(ielec)], comm,
                        numpy.mod(ielec, comm.size), 1, compress=blosc_compress
                    )
        elif gpass == 1:
            for ielec in range(comm.rank, N_e, comm.size):
                result['times_' + str(ielec)] = numpy.copy(result['loc_times_' + str(ielec)])

        if comm.rank == 0:
            if gpass == 0:
                print_and_log(["Estimating amplitudes distributions..."], 'default', logger)
            elif gpass == 1:
                print_and_log(["Computing density estimations..."], 'default', logger)
            else:
                print_and_log(["Refining density estimations..."], 'default', logger)
            if not os.path.exists(plot_path):
                os.makedirs(plot_path)

        if gpass == 1:
            dist_file = tempfile.NamedTemporaryFile()
            tmp_file = os.path.join(tmp_path_loc, os.path.basename(dist_file.name)) + '.hdf5'
            dist_file.close()
            result['dist_file'] = tmp_file
            tmp_h5py = h5py.File(result['dist_file'], 'w', libver='earliest')
            print_and_log(["Node %d will use temp file %s" % (comm.rank, tmp_file)], 'debug', logger)
        elif gpass > 1:
            tmp_h5py = h5py.File(result['dist_file'], 'r', libver='earliest')

        to_explore = range(comm.rank, N_e, comm.size)
        sys.stderr.flush()

        if (comm.rank == 0) and gpass == nb_repeats:
            print_and_log(["Running density-based clustering..."], 'default', logger)
            to_explore = get_tqdm_progressbar(to_explore)

        for ielec in to_explore:

            for p in search_peaks:
                cluster_results[p][ielec] = {}

                if gpass == 0:
                    if len(result['tmp_%s_' % p + str(ielec)]) > 1:

                        # Need to estimate the number of spikes.
                        ratio = nb_chunks / float(result['nb_chunks_%s_' % p + str(ielec)])
                        ampmin = numpy.min(result['tmp_%s_' % p + str(ielec)])
                        ampmax = numpy.max(result['tmp_%s_' % p + str(ielec)])
                        if p == 'pos':
                            if matched_filter:
                                bound = matched_thresholds_pos[ielec]
                            else:
                                bound = thresholds[ielec]
                            if bound < ampmax:
                                bins = numpy.linspace(bound, 1.5 * ampmax, nb_ss_bins - 1).tolist()
                                bins = [-numpy.inf] + bins + [numpy.inf]
                            else:
                                bins = numpy.linspace(bound, bound * 10, nb_ss_bins - 1).tolist()
                                bins = [-numpy.inf] + bins + [numpy.inf]

                        elif p == 'neg':
                            if matched_filter:
                                bound = -matched_thresholds_neg[ielec]
                            else:
                                bound = -thresholds[ielec]
                            if ampmin < bound:
                                bins = numpy.linspace(1.5 * ampmin, bound, nb_ss_bins - 1).tolist()
                                bins = [-numpy.inf] + bins + [numpy.inf]
                            else:
                                bins = numpy.linspace(10 * bound, bound, nb_ss_bins - 1).tolist()
                                bins = [-numpy.inf] + bins + [numpy.inf]

                        a, b = numpy.histogram(result['tmp_%s_' % p + str(ielec)], bins)
                        nb_spikes = numpy.sum(a)
                        a = a / float(nb_spikes)

                        z = a[a > 0]
                        c = 1.0 / numpy.min(z)
                        d = 1. / (c * a)
                        d = numpy.minimum(1, d)
                        d /= numpy.sum(d)
                        twist = numpy.sum(a * d)
                        factor = twist * c
                        rejection_curve = numpy.minimum(0.95, factor * a)

                        if ratio > 1:
                            target_max = 1 - (1 - rejection_curve.max()) / ratio
                            rejection_curve *= target_max / (rejection_curve.max())

                        result['hist_%s_' % p + str(ielec)] = rejection_curve
                        result['bounds_%s_' % p + str(ielec)] = b

                        # if make_plots not in ['None', '']:
                        #     save     = [plot_path, '%s_%d.%s' %(p, ielec, make_plots)]
                        #     plot.view_rejection(a, b[1:], result['hist_%s_'%p + str(ielec)], save=save)

                    else:
                        smart_searches[p][ielec] = 0

                    if smart_searches[p][ielec] > 0:
                        print_and_log(['Smart search is actived on channel %d' % ielec], 'debug', logger)

                elif gpass == 1:
                    if len(result['data_%s_' % p + str(ielec)]) >= 1:

                        if result['pca_%s_' % p + str(ielec)] is None:

                            if result['data_%s_' % p + str(ielec)].shape[1] > sub_output_dim:
                                pca = PCA(sub_output_dim)
                                pca.fit(result['data_%s_' % p + str(ielec)])
                                result['pca_%s_' % p + str(ielec)] = pca.components_.T.astype(numpy.float32)
                                print_and_log([
                                    "The variance explained by local PCA on electrode %s from %d %s spikes is %g with %d dimensions"
                                    % (ielec, len(result['data_%s_' % p + str(ielec)]), p, numpy.sum(pca.explained_variance_ratio_), result['pca_%s_' % p + str(ielec)].shape[1])
                                ], 'debug', logger)
                            else:
                                dimension = result['data_%s_' % p + str(ielec)].shape[1]
                                result['pca_%s_' % p + str(ielec)] = numpy.zeros(
                                    (dimension, sub_output_dim), dtype=numpy.float32
                                )
                                result['pca_%s_' % p + str(ielec)][numpy.arange(dimension), numpy.arange(dimension)] = 1

                        result['sub_%s_' % p + str(ielec)] = numpy.dot(
                            result['data_%s_' % p + str(ielec)], result['pca_%s_' % p + str(ielec)]
                        )

                        rho, dist, sdist = algo.compute_rho(result['sub_%s_' % p + str(ielec)], mratio=m_ratio)
                        result['rho_%s_' % p + str(ielec)] = rho
                        result['sdist_%s_' % p + str(ielec)] = sdist
                        if hdf5_compress:
                            tmp_h5py.create_dataset(
                                'dist_%s_' % p + str(ielec), data=dist.distances, chunks=True, compression='gzip'
                            )
                        else:
                            tmp_h5py.create_dataset('dist_%s_' % p + str(ielec), data=dist.distances, chunks=True)
                        del dist, rho
                    else:
                        if result['pca_%s_' % p + str(ielec)] is None:
                            n_neighb = len(edges[nodes[ielec]])
                            dimension = basis['proj_%s' % p].shape[1] * n_neighb
                            nb_max = min(dimension, sub_output_dim)
                            result['pca_%s_' % p + str(ielec)] = numpy.zeros(
                                (dimension, sub_output_dim), dtype=numpy.float32
                            )
                            result['pca_%s_' % p + str(ielec)][numpy.arange(nb_max), numpy.arange(nb_max)] = 1
                        result['rho_%s_' % p + str(ielec)] = numpy.zeros(0, dtype=numpy.float32)
                        result['sub_%s_' % p + str(ielec)] = numpy.zeros((0, sub_output_dim), dtype=numpy.float32)
                        result['sdist_%s_' % p + str(ielec)] = numpy.zeros(0, dtype=numpy.float32)
                else:
                    if len(result['tmp_%s_' % p + str(ielec)]) > 1:

                        rho, sdist = algo.compute_rho(
                            result['sub_%s_' % p + str(ielec)], update=(result['tmp_%s_' % p + str(ielec)], result['sdist_%s_' % p + str(ielec)]), mratio=m_ratio
                        )
                        result['rho_%s_' % p + str(ielec)] = rho
                        result['sdist_%s_' % p + str(ielec)] = sdist
                        del rho

                if gpass == nb_repeats:  # i.e. last pass (during which clustering is done)

                    if 'tmp_%s_' % p + str(ielec) in result:
                        result.pop('tmp_%s_' % p + str(ielec))
                    n_data = len(result['data_%s_' % p + str(ielec)])
                    n_min = n_abs_min

                    if p == 'pos':
                        flag = 'positive'
                    elif p == 'neg':
                        flag = 'negative'

                    if n_data > 1:
                        dist = tmp_h5py.get('dist_%s_' % p + str(ielec))[:]
                        result['rho_%s_' % p + str(ielec)] = \
                            -result['rho_%s_' % p + str(ielec)] + result['rho_%s_' % p + str(ielec)].max()

                        # Now we perform the clustering.
                        cluster_results[p][ielec]['groups'], r, d, c = algo.clustering_by_density(
                            result['rho_%s_' % p + str(ielec)], dist, n_min=n_min, alpha=sensitivity
                        )
                        result['delta_%s_' % p + str(ielec)] = d  # i.e. save delta values

                        # Now we perform a merging step, for clusters that look too similar.
                        old_allocation = np.copy(cluster_results[p][ielec]['groups'])
                        cluster_results[p][ielec]['groups'], merged, merge_history = algo.merging(
                            cluster_results[p][ielec]['groups'], merging_method,
                            merging_param, result['sub_%s_' % p + str(ielec)]
                        )

                        # Remove clusters without a sufficient number of points.
                        idx_clusters, counts = numpy.unique(cluster_results[p][ielec]['groups'], return_counts=True)
                        count = 0
                        to_remove = []
                        for label, cluster_size in zip(idx_clusters, counts):
                            if (label > -1) and (cluster_size < n_min):
                                tmp = cluster_results[p][ielec]['groups'] == label
                                cluster_results[p][ielec]['groups'][tmp] = -1
                                to_remove += [count]
                            count += 1
                        c = numpy.delete(c, to_remove)  # update the cluster labels

                        # Sanity plots for clusters.
                        if make_plots not in ['None', '']:
                            save = [plot_path, '%s_%d.%s' % (p, ielec, make_plots)]
                            injected = None
                            if test_clusters:
                                injected = numpy.zeros(len(result['data_%s_' % p + str(ielec)]), dtype=numpy.bool)
                                key = 'spikes_' + str(ielec)
                                thresh = 2
                                if key in injected_spikes:
                                    for icount, spike in enumerate(result['loc_times_' + str(ielec)]):
                                        idx = numpy.where(
                                            numpy.abs(spike - injected_spikes['spikes_' + str(ielec)]) < thresh
                                        )[0]
                                        if len(idx) > 0:
                                            if icount < (len(injected) - 1):
                                                injected[icount] = True

                            plot.view_clusters(
                                result['sub_%s_' % p + str(ielec)], result['rho_%s_' % p + str(ielec)],
                                result['delta_%s_' % p + str(ielec)], c,
                                cluster_results[p][ielec]['groups'], injected=injected,
                                save=save, alpha=sensitivity
                            )

                        # Sanity plots for local merges.
                        if debug_plots not in ['None', '']:
                            # Retrieve waveforms data.
                            n_neighbors = len(edges[nodes[ielec]])
                            indices = nodes_indices[ielec]
                            data = result['data_%s_' % p + str(ielec)]
                            data = data.reshape((n_data, basis['proj_%s' % p].shape[1], n_neighbors))
                            idx = numpy.where(indices == ielec)[0][0]
                            sub_data = numpy.take(data, idx, axis=2)
                            waveforms_data = numpy.dot(sub_data, basis['rec_%s' % p])
                            # Retrieve clusters data.
                            clusters_data = result['sub_%s_' % p + str(ielec)]
                            # Retrieve new allocation.
                            new_allocation = cluster_results[p][ielec]['groups']
                            # Define output path.
                            save = [plot_path, '%s_%d' % (p, ielec), debug_plots]
                            # Call plot function.
                            plot.view_local_merges(
                                waveforms_data,
                                clusters_data,
                                old_allocation,
                                new_allocation,
                                merge_history,
                                save=save
                            )

                        keys = [
                            'loc_times_' + str(ielec),
                            'all_times_' + str(ielec),
                        ]
                        if not debug: 
                            keys += [
                                'rho_%s_' % p + str(ielec),
                                'delta_%s_' % p + str(ielec),
                            ]

                        for key in keys:
                            if key in result:
                                result.pop(key)
                        mask = numpy.where(cluster_results[p][ielec]['groups'] > -1)[0]
                        cluster_results[p][ielec]['n_clus'] = \
                            len(numpy.unique(cluster_results[p][ielec]['groups'][mask]))
                        n_clusters = []
                        result['clusters_%s_' % p + str(ielec)] = cluster_results[p][ielec]['groups']

                        for i in numpy.unique(cluster_results[p][ielec]['groups'][mask]):
                            n_clusters += [numpy.sum(cluster_results[p][ielec]['groups'][mask] == i)]

                        line = [
                            "Node %d: %d-%d %s templates on channel %d from %d spikes: %s"
                            % (comm.rank, merged[0], merged[1], flag, ielec, n_data, str(n_clusters))
                        ]
                        print_and_log(line, 'debug', logger)
                        local_mergings += merged[1]
                        del dist, d, c
                    else:
                        cluster_results[p][ielec]['groups'] = numpy.zeros(0, dtype=numpy.int32)
                        cluster_results[p][ielec]['n_clus'] = 0
                        result['clusters_%s_' % p + str(ielec)] = numpy.zeros(0, dtype=numpy.int32)
                        result['delta_%s_' % p + str(ielec)] = numpy.zeros(0, dtype=numpy.float32)
                        line = ["Node %d: not enough %s spikes on channel %d" % (comm.rank, flag, ielec)]
                        print_and_log(line, 'debug', logger)

                    local_nb_clusters += cluster_results[p][ielec]['n_clus']

        if gpass >= 1:
            tmp_h5py.close()
        gpass += 1

    # Final concatenations (for efficiency).
    for elec in range(N_e):
        result['peaks_' + str(elec)] = numpy.concatenate(result['peaks_' + str(elec)])

    sys.stderr.flush()
    try:
        os.remove(result['dist_file'])
    except Exception:
        pass

    comm.Barrier()

    gdata = gather_array(numpy.array([local_hits], dtype=numpy.float32), comm, 0)
    gdata2 = gather_array(numpy.array([local_mergings], dtype=numpy.float32), comm, 0)
    gdata3 = gather_array(numpy.array([local_nb_clusters], dtype=numpy.float32), comm, 0)

    mean_channels = 0

    if comm.rank == 0:
        total_hits = int(numpy.sum(gdata))
        total_mergings = int(numpy.sum(gdata2))
        total_nb_clusters = int(numpy.sum(gdata3))
        lines = [
            "Number of clusters found : %d" % total_nb_clusters,
            "Number of local merges   : %d (method %s, param %g)" % (total_mergings, merging_method, merging_param)
        ]
        if few_elts:
            lines += ["Not enough spikes gathered: -put safety_space=False?"]
            if numpy.any(sdata > 0):
                lines += ["                            -remove smart_search?"]

        print_and_log(lines, 'info', logger)
        print_and_log(["Estimating the templates with the %s procedure ..." % extraction], 'default', logger)
        if use_savgol:
            print_and_log(["Templates will be smoothed by Savitzky Golay Filtering ..."], 'debug', logger)

    # Now we perform the extraction of the templates.
    if extraction in ['median-raw', 'mean-raw']:

        total_nb_clusters = int(comm.bcast(numpy.array([int(numpy.sum(gdata3))], dtype=numpy.int32), root=0)[0])
        offsets = numpy.zeros(comm.size, dtype=numpy.int32)
        for i in range(comm.size-1):
            offsets[i + 1] = comm.bcast(numpy.array([local_nb_clusters], dtype=numpy.int32), root=i)
        node_pad = numpy.sum(offsets[:comm.rank+1])

        if parallel_hdf5:
            hfile = h5py.File(file_out_suff + '.templates.hdf5', 'w', driver='mpio', comm=comm, libver='earliest')
            norms = hfile.create_dataset('norms', shape=(2 * total_nb_clusters, ), dtype=numpy.float32, chunks=True)
            electrodes = hfile.create_dataset('electrodes', shape=(total_nb_clusters, ), dtype=numpy.int32, chunks=True)
            amps_lims = hfile.create_dataset('limits', shape=(total_nb_clusters, 2), dtype=numpy.float32, chunks=True)
            supports = hfile.create_dataset('supports', shape=(total_nb_clusters, N_e), dtype=numpy.bool, chunks=True)
            g_count = node_pad
            g_offset = total_nb_clusters
        else:
            hfile = h5py.File(file_out_suff + '.templates-%d.hdf5' % comm.rank, 'w', libver='earliest')
            electrodes = hfile.create_dataset('electrodes', shape=(local_nb_clusters, ), dtype=numpy.int32, chunks=True)
            norms = hfile.create_dataset('norms', shape=(2*local_nb_clusters, ), dtype=numpy.float32, chunks=True)
            amps_lims = hfile.create_dataset('limits', shape=(local_nb_clusters, 2), dtype=numpy.float32, chunks=True)
            supports = hfile.create_dataset('supports', shape=(local_nb_clusters, N_e), dtype=numpy.bool, chunks=True)
            g_count = 0
            g_offset = local_nb_clusters

        temp_x = [numpy.zeros(0, dtype=numpy.uint32)]
        temp_y = [numpy.zeros(0, dtype=numpy.uint32)]
        temp_data = [numpy.zeros(0, dtype=numpy.float32)]
        templates_to_remove = [numpy.empty(0, dtype=numpy.int32)]

        comm.Barrier()
        cfile = h5py.File(file_out_suff + '.clusters-%d.hdf5' % comm.rank, 'w', libver='earliest')
        count_templates = node_pad

        data_file.close()

        to_explore = range(comm.rank, N_e, comm.size)

        if comm.rank == 0:
            to_explore = get_tqdm_progressbar(to_explore)

        for ielec in to_explore:

            nb_dim_kept = numpy.inf
            for p in search_peaks:
                nb_dim_kept = min(nb_dim_kept, result['pca_%s_' % p + str(ielec)].shape[1])

            result['data_' + str(ielec)] = [numpy.empty((0, nb_dim_kept), dtype=numpy.float32)]
            if debug:
                result['rho_' + str(ielec)] = [numpy.empty(0, dtype=numpy.float32)]
                result['delta_' + str(ielec)] = [numpy.empty(0, dtype=numpy.float32)]
            indices = inv_nodes[nodes]
            sindices = nodes_indices[ielec]
            n_neighb = len(sindices)

            for p in search_peaks:

                # print "Dealing with cluster", ielec
                n_data = len(result['data_%s_' % p + str(ielec)])
                data = result['data_%s_' % p + str(ielec)].reshape(n_data, basis['proj_%s' % p].shape[1], n_neighb)
                loc_pad = count_templates
                myamps = []
                mask = numpy.where(cluster_results[p][ielec]['groups'] > -1)[0]

                if p == 'pos':
                    myslice2 = numpy.where(result['peaks_' + str(ielec)] == 0)[0]
                elif p == 'neg':
                    myslice2 = numpy.where(result['peaks_' + str(ielec)] == 1)[0]
                else:
                    raise ValueError("unexpected value")

                loc_times = numpy.take(result['times_' + str(ielec)], myslice2)
                loc_clusters = numpy.take(cluster_results[p][ielec]['groups'], mask)

                # TODO test.

                # Pre-compute all the first components (for each group).
                all_first_flat = {}

                for group_1 in numpy.unique(loc_clusters):

                    myslice = numpy.where(cluster_results[p][ielec]['groups'] == group_1)[0]

                    if extraction == 'median-raw':
                        # labels_i = numpy.random.permutation(myslice)[:250]
                        numpy.random.seed(42)
                        labels_i = numpy.random.permutation(myslice)[:500]
                        times_i = numpy.take(loc_times, labels_i)
                        sub_data_raw = io.get_stas(params, times_i, labels_i, ielec, neighs=indices, nodes=nodes, pos=p)
                        first_component = numpy.median(sub_data_raw, 0)
                    elif extraction == 'mean-raw':
                        # labels_i = numpy.random.permutation(myslice)[:250]
                        numpy.random.seed(42)
                        labels_i = numpy.random.permutation(myslice)[:500]
                        times_i = numpy.take(loc_times, labels_i)
                        sub_data_raw = io.get_stas(params, times_i, labels_i, ielec, neighs=indices, nodes=nodes, pos=p)
                        first_component = numpy.mean(sub_data_raw, 0)
                    else:
                        raise ValueError("unexpected value %s" % extraction)

                    if use_savgol and savgol_window > 3:
                        tmp_fast = scipy.signal.savgol_filter(first_component, savgol_window, 3, axis=1)
                        tmp_slow = scipy.signal.savgol_filter(first_component, 3 * savgol_window, 3, axis=1)
                        first_component = savgol_filter * tmp_fast + (1 - savgol_filter) * tmp_slow

                    if comp_templates:  # i.e. template compression active
                        local_stds = numpy.std(first_component, 1)
                        to_delete = numpy.where(local_stds / stds[indices] < sparsify)[0]
                        first_component[to_delete, :] = 0

                    x, y, z = sub_data_raw.shape
                    sub_data_raw[:, to_delete, :] = 0
                    sub_data_flat_raw = sub_data_raw.reshape(x, y * z)
                    first_flat = first_component.reshape(y * z, 1)
                    amplitudes = numpy.dot(sub_data_flat_raw, first_flat)
                    amplitudes /= numpy.sum(first_flat ** 2)
                    center = numpy.median(amplitudes)

                    # We are rescaling the template such that median amplitude is exactly 1
                    # This is changed because of the smoothing
                    first_component *= center

                    first_flat = first_component.reshape(y * z, 1)

                    all_first_flat[group_1] = first_flat

                # Pre-compute all the amplitudes and scalar products (for each pair of group)

                all_amplitudes = {}
                all_scalar_products = {}
                all_dot_products = {}

                for group_1 in numpy.unique(loc_clusters):

                    myslice = numpy.where(cluster_results[p][ielec]['groups'] == group_1)[0]

                    if extraction == 'median-raw':
                        # labels_i = numpy.random.permutation(myslice)[:250]
                        numpy.random.seed(42)
                        labels_i = numpy.random.permutation(myslice)[:500]
                        times_i = numpy.take(loc_times, labels_i)
                        sub_data_raw = io.get_stas(params, times_i, labels_i, ielec, neighs=indices, nodes=nodes, pos=p)
                        first_component = numpy.median(sub_data_raw, 0)
                    elif extraction == 'mean-raw':
                        # labels_i = numpy.random.permutation(myslice)[:250]
                        numpy.random.seed(42)
                        labels_i = numpy.random.permutation(myslice)[:500]
                        times_i = numpy.take(loc_times, labels_i)
                        sub_data_raw = io.get_stas(params, times_i, labels_i, ielec, neighs=indices, nodes=nodes, pos=p)
                        first_component = numpy.mean(sub_data_raw, 0)
                    else:
                        raise ValueError("unexpected value %s" % extraction)

                    if use_savgol and savgol_window > 3:
                        tmp_fast = scipy.signal.savgol_filter(first_component, savgol_window, 3, axis=1)
                        tmp_slow = scipy.signal.savgol_filter(first_component, 3 * savgol_window, 3, axis=1)
                        first_component = savgol_filter * tmp_fast + (1 - savgol_filter) * tmp_slow

                    if comp_templates:
                        local_stds = numpy.std(first_component, 1)
                        to_delete = numpy.where(local_stds / stds[indices] < sparsify)[0]
                        first_component[to_delete, :] = 0

                    # if p == 'neg':
                    #     tmpidx = numpy.unravel_index(first_component.argmin(), first_component.shape)
                    #     ratio = -thresholds[indices[tmpidx[0]]] / first_component[tmpidx[0]].min()
                    # elif p == 'pos':
                    #     tmpidx = numpy.unravel_index(first_component.argmax(), first_component.shape)
                    #     ratio = thresholds[indices[tmpidx[0]]] / first_component[tmpidx[0]].max()
                    #
                    # shift = template_shift - tmpidx[1]
                    # is_noise = len(indices) == len(to_delete) or (1 / ratio) < noise_thresh

                    x, y, z = sub_data_raw.shape
                    sub_data_raw[:, to_delete, :] = 0
                    sub_data_flat_raw = sub_data_raw.reshape(x, y * z)

                    all_amplitudes[group_1] = {}
                    all_scalar_products[group_1] = {}
                    all_dot_products[group_1] = {}

                    for group_2 in numpy.unique(loc_clusters):

                        first_flat = all_first_flat[group_2]
                        dot_products = numpy.dot(sub_data_flat_raw, first_flat).flatten()
                        amplitudes = dot_products / numpy.sum(first_flat ** 2)
                        scalar_products = dot_products / np.square(numpy.sum(first_flat ** 2))

                        all_amplitudes[group_1][group_2] = amplitudes
                        all_scalar_products[group_1][group_2] = scalar_products
                        all_dot_products[group_1][group_2] = dot_products

                # TODO end.

                for group in numpy.unique(loc_clusters):
                    electrodes[g_count] = ielec
                    myslice = numpy.where(cluster_results[p][ielec]['groups'] == group)[0]

                    if extraction == 'median-raw':
                        # labels_i = numpy.random.permutation(myslice)[:250]
                        numpy.random.seed(42)
                        labels_i = numpy.random.permutation(myslice)[:500]
                        times_i = numpy.take(loc_times, labels_i)
                        sub_data_raw = io.get_stas(params, times_i, labels_i, ielec, neighs=indices, nodes=nodes, pos=p)
                        first_component = numpy.median(sub_data_raw, 0)
                    elif extraction == 'mean-raw':                
                        # labels_i = numpy.random.permutation(myslice)[:250]
                        numpy.random.seed(42)
                        labels_i = numpy.random.permutation(myslice)[:500]
                        times_i = numpy.take(loc_times, labels_i)
                        sub_data_raw = io.get_stas(params, times_i, labels_i, ielec, neighs=indices, nodes=nodes, pos=p)
                        first_component = numpy.mean(sub_data_raw, 0)
                    else:
                        raise ValueError("unexpected value %s" % extraction)

                    if use_savgol and savgol_window > 3:
                        tmp_fast = scipy.signal.savgol_filter(first_component, savgol_window, 3, axis=1)
                        tmp_slow = scipy.signal.savgol_filter(first_component, 3 * savgol_window, 3, axis=1)
                        first_component = savgol_filter * tmp_fast + (1 - savgol_filter) * tmp_slow

                    mean_channels += len(indices)
                    if comp_templates:
                        local_stds = numpy.std(first_component, 1)
                        to_delete = numpy.where(local_stds / stds[indices] < sparsify)[0]
                        first_component[to_delete, :] = 0
                        mean_channels -= len(to_delete)

                    if p == 'neg':
                        tmpidx = numpy.unravel_index(first_component.argmin(), first_component.shape)
                        ratio = -thresholds[indices[tmpidx[0]]] / first_component[tmpidx[0]].min()
                    elif p == 'pos':
                        tmpidx = numpy.unravel_index(first_component.argmax(), first_component.shape)
                        ratio = thresholds[indices[tmpidx[0]]] / first_component[tmpidx[0]].max()

                    shift = template_shift - tmpidx[1]
                    is_noise = len(indices) == len(to_delete) or (1 / ratio) < noise_thresh

                    if is_noise or (np.abs(shift) > template_shift / 4):
                        templates_to_remove.append(numpy.array([count_templates], dtype='int32'))
                        myamps += [[0, 10]]
                    else:

                        x, y, z = sub_data_raw.shape
                        sub_data_raw[:, to_delete, :] = 0
                        sub_data_flat_raw = sub_data_raw.reshape(x, y*z)
                        first_flat = first_component.reshape(y*z, 1)
                        amplitudes = numpy.dot(sub_data_flat_raw, first_flat)
                        amplitudes /= numpy.sum(first_flat**2)
                        center = numpy.median(amplitudes)

                        # We are rescaling the template such that median amplitude is exactly 1
                        # This is changed because of the smoothing
                        first_component *= center
                        ratio /= center

                        templates = numpy.zeros((N_e, N_t), dtype=numpy.float32)
                        if shift > 0:
                            templates[:, shift:] = first_component[:, :-shift]
                        elif shift < 0:
                            templates[:, :shift] = first_component[:, -shift:]
                        else:
                            templates[:, :] = first_component

                        first_flat = first_component.reshape(y * z, 1)
                        amplitudes = numpy.dot(sub_data_flat_raw, first_flat)
                        amplitudes /= numpy.sum(first_flat ** 2)
                        variation = numpy.median(numpy.abs(amplitudes - 1))

                        templates = templates.ravel()
                        dx = templates.nonzero()[0].astype(numpy.uint32)
                        temp_x.append(dx)
                        temp_y.append(count_templates * numpy.ones(len(dx), dtype=numpy.uint32))
                        temp_data.append(templates[dx])

                        supports[g_count] = ~numpy.in1d(indices, to_delete)
                        norms[g_count] = numpy.sqrt(numpy.sum(templates.ravel() ** 2) / n_scalar)

                        distance = \
                            min(0, numpy.abs(first_component[tmpidx[0], tmpidx[1]]) - thresholds[indices[tmpidx[0]]])
                        noise_limit = max([0, distance + mads[indices[tmpidx[0]]]])

                        amp_min = 1 - min([dispersion[0] * variation, noise_limit])
                        amp_max = 1 + min([dispersion[1] * variation, mads[indices[tmpidx[0]]]])

                        amps_lims[g_count] = [amp_min, amp_max]
                        myamps += [[amp_min, amp_max]]

                        # TODO quick sanity plot (amplitude values used to set amplitude limits).
                        if make_plots not in ['None', '']:
                            import matplotlib.pyplot as plt
                            fig, axes = plt.subplots(
                                nrows=4, ncols=2, squeeze=False, gridspec_kw={'width_ratios': [0.8, 0.2]},
                                figsize=[2.0 * 6.4, 2.0 * 4.8]
                            )
                            # x = np.arange(0, amplitudes.size)
                            # y = amplitudes
                            # ...
                            ax = axes[0, 0]
                            for other_group in numpy.unique(loc_clusters):
                                # other_amplitudes = all_amplitudes[group][other_group]  # i.e. points du groupe projetés sur les autres groupes
                                other_amplitudes = all_amplitudes[other_group][group]  # i.e. points des autres groupes projetés sur le groupe
                                x_ = np.arange(0, other_amplitudes.size)
                                y_ = other_amplitudes
                                color_ = 'C{}'.format(other_group % 10)
                                ax.scatter(x_, y_, s=2**2, color=color_)
                            # ax.scatter(x, y, s=1**2, color='black')
                            ax.axhline(y=amp_min, color='C{}'.format(group % 10), linewidth=0.3)
                            ax.axhline(y=1.0, color='gray', linewidth=0.3)
                            ax.axhline(y=amp_max, color='C{}'.format(group % 10), linewidth=0.3)
                            # ax.set_xlabel("point")
                            ax.set_ylabel("amplitude")
                            ax.set_title("template of cluster of interest vs snippets of other clusters")
                            xlim = ax.get_xlim()
                            range_ = ax.get_ylim()
                            # ...
                            ax = axes[0, 1]
                            # ax.hist(y, bins=50, range=range_, color='black', orientation='horizontal')
                            # ax.hist(y, bins=50, range=range_, color='C{}'.format(group % 10), orientation='horizontal')
                            for other_group in numpy.unique(loc_clusters):
                                # other_amplitudes = all_amplitudes[group][other_group]  # i.e. points du groupe projetés sur les autres groupes
                                other_amplitudes = all_amplitudes[other_group][group]  # i.e. points des autres groupes projetés sur le groupe
                                y_ = other_amplitudes
                                color_ = 'C{}'.format(other_group % 10)
                                ax.hist(y_, bins=50, range=range_, color=color_, orientation='horizontal', alpha=0.4)
                            ax.axhline(y=amp_min, color='C{}'.format(group % 10), linewidth=0.3)
                            # ax.axhline(y=amp_min, color='gray', linewidth=0.3)
                            ax.axhline(y=1.0, color='gray', linewidth=0.3)
                            ax.axhline(y=amp_max, color='C{}'.format(group % 10), linewidth=0.3)
                            # ax.axhline(y=amp_max, color='gray', linewidth=0.3)
                            ax.set_ylim(*range_)
                            ax.set_yticklabels([])
                            # ...
                            ax = axes[1, 0]
                            for other_group in numpy.unique(loc_clusters):
                                other_scalar_products = all_scalar_products[group][other_group]  # i.e. points du groupe projetés sur les autres groupes
                                # other_scalar_products = all_scalar_products[other_group][group]  # i.e. points des autres groupes projetés sur le groupe
                                x_ = np.arange(0, other_scalar_products.size)
                                y_ = other_scalar_products
                                color_ = 'C{}'.format(other_group % 10)
                                ax.scatter(x_, y_, s=2**2, color=color_)
                            ax.set_xlim(*xlim)
                            range_ = ax.get_ylim()
                            # ax.set_xlabel("point")
                            ax.set_ylabel("scalar product")
                            ax.set_title("snippets of cluster of interest vs templates of other clusters")
                            # ...
                            ax = axes[1, 1]
                            # ax.axis('off')
                            for other_group in numpy.unique(loc_clusters):
                                other_scalar_products = all_scalar_products[group][other_group]  # i.e. points du groupe projetés sur les autres groupes
                                # other_scalar_products = all_scalar_products[other_group][group]  # i.e. points des autres groupes projetés sur le groupe
                                y_ = other_scalar_products
                                color_ = 'C{}'.format(other_group % 10)
                                ax.hist(y_, bins=50, range=range_, color=color_, orientation='horizontal', alpha=0.4)
                            ax.set_ylim(*range_)
                            ax.set_yticklabels([])
                            # ...
                            ax = axes[2, 0]
                            dot_products_of_interest = all_dot_products[group][group]
                            for other_group in numpy.unique(loc_clusters):
                                other_dot_products = all_dot_products[group][other_group]  # i.e. points du groupe projetés sur les autres groupes
                                # other_dot_products = all_dot_products[other_group][group]  # i.e. points des autres groupes projetés sur le groupe
                                x_ = np.arange(0, other_dot_products.size)
                                y_ = other_dot_products
                                color_ = 'C{}'.format(other_group % 10)
                                selection = dot_products_of_interest <= other_dot_products
                                ax.scatter(x_[selection], y_[selection], s=2 ** 2, color=color_, alpha=1.0)
                                selection = dot_products_of_interest > other_dot_products
                                ax.scatter(x_[selection], y_[selection], s=2 ** 2, color=color_, alpha=0.1)
                            ax.set_xlim(*xlim)
                            range_ = ax.get_ylim()
                            # ax.set_xlabel("point")
                            ax.set_ylabel("dot product")
                            ax.set_title("snippets of cluster of interest vs templates of other clusters")
                            # ...
                            ax = axes[2, 1]
                            dot_products_of_interest = all_dot_products[group][group]
                            # ax.axis('off')
                            for other_group in numpy.unique(loc_clusters):
                                color_ = 'C{}'.format(other_group % 10)
                                other_dot_products = all_dot_products[group][other_group]  # i.e. points du groupe projetés sur les autres groupes
                                # other_dot_products = all_dot_products[other_group][group]  # i.e. points des autres groupes projetés sur le groupe
                                y_ = other_dot_products
                                ax.hist(y_, bins=50, range=range_, facecolor='None', edgecolor=color_,histtype='stepfilled', orientation='horizontal', alpha=0.4)
                                selection = dot_products_of_interest <= other_dot_products
                                if y_[selection].size == 0:
                                    continue
                                ax.hist(y_[selection], bins=50, range=range_, color=color_, histtype='stepfilled', orientation='horizontal', alpha=0.4)
                            ax.set_ylim(*range_)
                            ax.set_yticklabels([])
                            # ...
                            ax = axes[3, 0]
                            dot_products_of_interest = all_dot_products[group][group]
                            for other_group in numpy.unique(loc_clusters):
                                color_ = 'C{}'.format(other_group % 10)
                                other_dot_products = all_dot_products[group][other_group]  # i.e. points du groupe projetés sur les autres groupes
                                # other_dot_products = all_dot_products[other_group][group]  # i.e. points des autres groupes projetés sur le groupe
                                other_amplitudes = all_amplitudes[group][other_group]  # i.e. points du groupe projetés sur les autres groupes
                                # other_amplitudes = all_amplitudes[other_group][group]  # i.e. points des autres groupes projetés sur le groupe
                                x_ = np.arange(0, other_amplitudes.size)
                                y_ = other_amplitudes
                                selection = dot_products_of_interest <= other_dot_products
                                ax.scatter(x_[selection], y_[selection], s=2 ** 2, color=color_, alpha=1.0)
                                selection = dot_products_of_interest > other_dot_products
                                ax.scatter(x_[selection], y_[selection], s=2 ** 2, color=color_, alpha=0.1)
                            ax.axhline(y=amp_min, color='C{}'.format(group % 10), linewidth=0.3)
                            ax.axhline(y=1.0, color='gray', linewidth=0.3)
                            ax.axhline(y=amp_max, color='C{}'.format(group % 10), linewidth=0.3)
                            ax.set_xlim(*xlim)
                            range_ = ax.get_ylim()
                            ax.set_xlabel("point")
                            ax.set_ylabel("amplitude")
                            ax.set_title("snippets of cluster of interest vs templates of other clusters")
                            # ...
                            ax = axes[3, 1]
                            # ax.axis('off')
                            dot_products_of_interest = all_dot_products[group][group]
                            for other_group in numpy.unique(loc_clusters):
                                color_ = 'C{}'.format(other_group % 10)
                                other_dot_products = all_dot_products[group][other_group]  # i.e. points du groupe projetés sur les autres groupes
                                # other_dot_products = all_dot_products[other_group][group]  # i.e. points des autres groupes projetés sur le groupe
                                other_amplitudes = all_amplitudes[group][other_group]  # i.e. points du groupe projetés sur les autres groupes
                                # other_amplitudes = all_amplitudes[other_group][group]  # i.e. points des autres groupes projetés sur le groupe
                                y_ = other_amplitudes
                                ax.hist(y_, bins=50, range=range_, facecolor='None', edgecolor=color_, histtype='stepfilled', orientation='horizontal', alpha=0.4)
                                selection = dot_products_of_interest <= other_dot_products
                                if y_[selection].size == 0:
                                    continue
                                ax.hist(y_[selection], bins=50, range=range_, color=color_, histtype='stepfilled', orientation='horizontal', alpha=0.4)
                            ax.axhline(y=amp_min, color='C{}'.format(group % 10), linewidth=0.3)
                            ax.axhline(y=1.0, color='gray', linewidth=0.3)
                            ax.axhline(y=amp_max, color='C{}'.format(group % 10), linewidth=0.3)
                            ax.set_ylim(*range_)
                            ax.set_yticklabels([])
                            ax.set_xlabel("nb. points")
                            # ...
                            plt.tight_layout()
                            # Save figure.
                            output_path = os.path.join(plot_path, '%s_e%d_g%d.%s' % (p, ielec, group, make_plots))
                            fig.savefig(output_path)
                            plt.close(fig)
                        # TODO end.

                        # TODO quick sanity plot (amplitude values used to set amplitude limits).
                        if make_plots not in ['None', '']:
                            import matplotlib.pyplot as plt
                            fig, axes = plt.subplots(
                                nrows=1, ncols=2, squeeze=False, gridspec_kw={'width_ratios': [0.8, 0.2]},
                                figsize=[1.0 * 6.4, 1.0 * 4.8]
                            )
                        # ...
                        ax = axes[0, 0]
                        # Plot the amplitude which should be accepted.
                        amplitudes_of_interest = all_amplitudes[group][group]
                        x_ = np.arange(0, amplitudes_of_interest.size)
                        y_ = amplitudes_of_interest
                        color_ = 'C{}'.format(group % 10)
                        ax.scatter(x_, y_, s=2 ** 2, color=color_, alpha=1.0)
                        ax.axhline(y=amp_min, color=color_, linewidth=0.3)
                        ax.axhline(y=1.0, color=color_, linewidth=0.3)
                        ax.axhline(y=amp_max, color=color_, linewidth=0.3)
                        # For each cluster...
                        for other_group in numpy.unique(loc_clusters):
                            if other_group == group:
                                continue
                            dot_products_of_reference = all_dot_products[other_group][other_group]
                            dot_products = all_dot_products[other_group][group]
                            amplitudes = all_amplitudes[other_group][group]
                            x_ = np.arange(0, amplitudes.size)
                            y_ = amplitudes
                            color_ = 'C{}'.format(other_group % 10)
                            # Plot the amplitudes which have not been considered.
                            selection = dot_products_of_reference > dot_products  # i.e. not considered
                            ax.scatter(x_[selection], y_[selection], s=2 ** 2, color=color_, alpha=0.1)
                            # Plot the amplitudes which should be rejected.
                            selection = dot_products_of_reference <= dot_products  # i.e. considered
                            ax.scatter(x_[selection], y_[selection], s=2 ** 2, color=color_, alpha=1.0)
                        # ...
                        ax = axes[0, 1]
                        ax.axis('off')  # TODO replace!
                        # ...
                        plt.tight_layout()
                        # Save figure.
                        output_path = os.path.join(plot_path, 'amplitude_interval_%s_e%d_g%d.%s' % (p, ielec, group, make_plots))
                        fig.savefig(output_path)
                        plt.close(fig)
                        # TODO end.
                        offset = total_nb_clusters + count_templates
                        sub_templates = numpy.zeros((N_e, N_t), dtype=numpy.float32)

                        if two_components:
                            # for i in range(x):
                            #     sub_data_flat_raw[i, :] -= amplitudes[i]*first_flat[:, 0]

                            # if len(sub_data_flat_raw) > 1:
                            #     pca              = PCA(1)
                            #     pca.fit(sub_data_flat_raw)
                            #     second_component = pca.components_.T.astype(numpy.float32).reshape(y, z)
                            # else:
                            #     second_component = sub_data_flat_raw.reshape(y, z)/numpy.sum(sub_data_flat_raw**2)

                            # if use_savgol and savgol_window > 3:
                            #     tmp_fast = scipy.signal.savgol_filter(second_component, savgol_window, 3, axis=1)
                            #     tmp_slow = scipy.signal.savgol_filter(second_component, 3*savgol_window, 3, axis=1)
                            #     second_component = savgol_filter*tmp_fast + (1 - savgol_filter)*tmp_slow

                            # if shift > 0:
                            #     sub_templates[indices, shift:] = second_component[:, :-shift]
                            # elif shift < 0:
                            #     sub_templates[indices, :shift] = second_component[:, -shift:]
                            # else:
                            #     sub_templates[indices, :] = second_component
                            sub_templates[:, :-1] = numpy.diff(templates.reshape(N_e, N_t))

                        sub_templates = sub_templates.ravel()
                        dx = sub_templates.nonzero()[0].astype(numpy.uint32)
                        temp_x.append(dx)
                        temp_y.append(offset * numpy.ones(len(dx), dtype=numpy.uint32))
                        temp_data.append(sub_templates[dx])

                        norms[g_count + g_offset] = numpy.sqrt(numpy.sum(sub_templates.ravel() ** 2) / n_scalar)

                    count_templates += 1
                    g_count += 1

                # Sanity plots of the waveforms.
                # if make_plots not in ['None', '']:
                #     if n_data > 1:
                #         save = [plot_path, '%s_%d.%s' % (p, ielec, make_plots)]
                #         idx = numpy.where(sindices == ielec)[0][0]
                #         sub_data = numpy.take(data, idx, axis=2)
                #         nb_temp = cluster_results[p][ielec]['n_clus']
                #         vidx = numpy.where((temp_y[-1] >= loc_pad) & (temp_y[-1] < loc_pad + nb_temp))[0]
                #         sub_tmp = scipy.sparse.csr_matrix(
                #             (temp_data[-1][vidx], (temp_x[-1][vidx], temp_y[-1][vidx] - loc_pad)),
                #             shape=(n_scalar, nb_temp)
                #         )
                #         sub_tmp = sub_tmp.toarray().reshape(N_e, N_t, nb_temp)
                #         sub_tmp = sub_tmp[ielec, :, :]
                #         plot.view_waveforms_clusters(
                #             numpy.dot(sub_data, basis['rec_%s' % p]), cluster_results[p][ielec]['groups'],
                #             thresholds[ielec], sub_tmp, numpy.array(myamps), save=save
                #         )

                nb_dim_found = result['sub_%s_' % p + str(ielec)].shape[1]

                if nb_dim_kept == nb_dim_found:
                    result['data_' + str(ielec)].append(result['sub_%s_' % p + str(ielec)])
                else:
                    sliced_data = result['sub_%s_' % p + str(ielec)][:, :nb_dim_kept]
                    result['data_' + str(ielec)].append(sliced_data)
                if len(result['clusters_' + str(ielec)]) > 0:
                    max_offset = numpy.int32(numpy.max(result['clusters_' + str(ielec)]) + 1)
                else:
                    max_offset = numpy.int32(0)

                if debug:
                    result['rho_' + str(ielec)].append(result['rho_%s_' % p + str(ielec)])
                    result['delta_' + str(ielec)].append(result['delta_%s_' % p + str(ielec)])

                mask = result['clusters_%s_' % p + str(ielec)] > -1
                result['clusters_%s_' % p + str(ielec)][mask] += max_offset
                result['clusters_' + str(ielec)] = numpy.concatenate(
                    (result['clusters_' + str(ielec)],
                     result['clusters_%s_' % p + str(ielec)])
                )

            del data

            # Final concatenations (for efficiency).
            result['data_' + str(ielec)] = numpy.concatenate(result['data_' + str(ielec)])
            if debug:
                result['rho_' + str(ielec)] = numpy.concatenate(result['rho_' + str(ielec)])
                result['delta_' + str(ielec)] = numpy.concatenate(result['delta_' + str(ielec)])

            all_indices = [numpy.empty(0, dtype=numpy.uint32)]
            for p in search_peaks:
                if p == 'pos':
                    target = 0
                elif p == 'neg':
                    target = 1
                else:
                    raise ValueError("unexpected value")
                indices_ = numpy.where(result['peaks_' + str(ielec)] == target)[0]
                all_indices.append(indices_)
            all_indices = numpy.concatenate(all_indices)  # i.e. concatenate once for efficiency

            result['times_' + str(ielec)] = result['times_' + str(ielec)][all_indices]
            result['peaks_' + str(ielec)] = result['peaks_' + str(ielec)][all_indices]

            io.write_datasets(cfile, to_write, result, ielec, compression=hdf5_compress)

        # At the end we should have a templates variable to store.
        cfile.close()
        del result, amps_lims
        sys.stderr.flush()

        temp_x = numpy.concatenate(temp_x)
        temp_y = numpy.concatenate(temp_y)
        temp_data = numpy.concatenate(temp_data)

        comm.Barrier()

        if local_nb_clusters > 0:
            mean_channels /= local_nb_clusters

        gdata4 = gather_array(numpy.array([mean_channels], dtype=numpy.float32), comm)

        templates_to_remove = np.concatenate(templates_to_remove)  # i.e. concatenate once for efficiency
        templates_to_remove = all_gather_array(templates_to_remove, comm, 0, dtype='int32')

        if comm.rank == 0:
            idx = numpy.where(gdata4 != 0)[0]
            mean_channels = numpy.mean(gdata4[idx])
            if mean_channels < 3 and params.getfloat('clustering', 'cc_merge') != 1:
                print_and_log(["Templates on few channels only, cc_merge set to 1 automatically"], 'info', logger)
        else:
            mean_channels = 0

        mean_channels = comm.bcast(numpy.array([int(mean_channels)], dtype=numpy.int32), root=0)[0]

        if mean_channels < 3:
            params.set('clustering', 'cc_merge', 1)

        # We need to gather the sparse arrays.
        temp_x = gather_array(temp_x, comm, dtype='uint32', compress=blosc_compress)
        temp_y = gather_array(temp_y, comm, dtype='uint32', compress=blosc_compress)
        temp_data = gather_array(temp_data, comm, compress=blosc_compress)

        if parallel_hdf5:
            if comm.rank == 0:
                rs = [
                    h5py.File(file_out_suff + '.clusters-%d.hdf5' % i, 'r', libver='earliest')
                    for i in range(comm.size)
                ]
                cfile = h5py.File(file_out_suff + '.clusters.hdf5', 'w', libver='earliest')
                io.write_datasets(cfile, ['electrodes'], {'electrodes': electrodes[:]})
                for i in range(comm.size):
                    for j in range(i, N_e, comm.size):
                        io.write_datasets(cfile, to_write, rs[i], j, compression=hdf5_compress)
                    rs[i].close()
                    os.remove(file_out_suff + '.clusters-%d.hdf5' % i)
                cfile.close()
            hfile.close()
        else:
            hfile.close()
            comm.Barrier()
            if comm.rank == 0:
                ts = [
                    h5py.File(file_out_suff + '.templates-%d.hdf5' % i, 'r', libver='earliest')
                    for i in range(comm.size)
                ]
                rs = [
                    h5py.File(file_out_suff + '.clusters-%d.hdf5' % i, 'r', libver='earliest')
                    for i in range(comm.size)
                ]
                result = {}
                hfile = h5py.File(file_out_suff + '.templates.hdf5', 'w', libver='earliest')
                cfile = h5py.File(file_out_suff + '.clusters.hdf5', 'w', libver='earliest')
                electrodes = hfile.create_dataset(
                    'electrodes', shape=(total_nb_clusters, ), dtype=numpy.int32, chunks=True
                )
                norms = hfile.create_dataset(
                    'norms', shape=(2 * total_nb_clusters, ), dtype=numpy.float32, chunks=True
                )
                amplitudes = hfile.create_dataset(
                    'limits', shape=(total_nb_clusters, 2), dtype=numpy.float32, chunks=True
                )
                supports = hfile.create_dataset(
                    'supports', shape=(total_nb_clusters, N_e), dtype=numpy.bool, chunks=True
                )
                count = 0
                for i in range(comm.size):
                    loc_norms = ts[i].get('norms')
                    middle = len(loc_norms) // 2
                    norms[count:count+middle] = loc_norms[:middle]
                    norms[total_nb_clusters+count:total_nb_clusters+count+middle] = loc_norms[middle:]
                    electrodes[count:count+middle] = ts[i].get('electrodes')
                    amplitudes[count:count+middle] = ts[i].get('limits')
                    supports[count:count+middle] = ts[i].get('supports')
                    count += middle
                    for j in range(i, N_e, comm.size):
                        io.write_datasets(cfile, to_write, rs[i], j, compression=hdf5_compress)
                    ts[i].close()
                    rs[i].close()
                    os.remove(file_out_suff + '.templates-%d.hdf5' % i)
                    os.remove(file_out_suff + '.clusters-%d.hdf5' % i)
                hfile.flush()  # we need to flush otherwise electrodes[:] refers to zeros and not the real values
                io.write_datasets(cfile, ['electrodes'], {'electrodes': electrodes[:]})
                hfile.close()
                cfile.close()

        if comm.rank == 0:
            hfile = h5py.File(file_out_suff + '.templates.hdf5', 'r+', libver='earliest')
            if hdf5_compress:
                hfile.create_dataset('temp_x', data=temp_x, compression='gzip')
                hfile.create_dataset('temp_y', data=temp_y, compression='gzip')
                hfile.create_dataset('temp_data', data=temp_data, compression='gzip')
            else:
                hfile.create_dataset('temp_x', data=temp_x)
                hfile.create_dataset('temp_y', data=temp_y)
                hfile.create_dataset('temp_data', data=temp_data)
            hfile.create_dataset('temp_shape', data=numpy.array([N_e, N_t, 2 * total_nb_clusters], dtype=numpy.int32))
            hfile.close()

    del temp_x, temp_y, temp_data

    import gc
    gc.collect()

    comm.Barrier()

    if len(templates_to_remove) > 0:

        if comm.rank == 0:
            print_and_log(
                ["Removing %d strongly shifted or noisy templates..." % len(templates_to_remove)], 'default', logger
            )

        if comm.rank == 0:
            result = io.load_data(params, 'clusters')
        else:
            result = []

        algo.slice_templates(params, to_remove=templates_to_remove)
        algo.slice_clusters(params, to_remove=templates_to_remove, result=result)

        del result

    comm.Barrier()

    total_nb_clusters = int(io.load_data(params, 'nb_templates') // 2)

    if total_nb_clusters > 0:

        if comm.rank == 0 and (params.getfloat('clustering', 'cc_merge') < 1):
            print_and_log(["Merging similar templates..."], 'default', logger)

        merged1 = algo.merging_cc(params, nb_cpu=nb_cpu, nb_gpu=nb_gpu, use_gpu=use_gpu)

        comm.Barrier()

        if remove_mixture:
            if comm.rank == 0:
                print_and_log(["Removing mixtures of templates..."], 'default', logger)
            merged2 = algo.delete_mixtures(params, nb_cpu=nb_cpu, nb_gpu=nb_gpu, use_gpu=use_gpu)
        else:
            merged2 = [0, 0]

    else:
        merged1 = [0, 0]
        merged2 = [0, 0]

    if comm.rank == 0:

        lines = [
            "Number of global merges    : %d" % merged1[1],
            "Number of mixtures removed : %d" % merged2[1],
        ]
        print_and_log(lines, 'info', logger)

    comm.Barrier()

    io.get_overlaps(params, erase=True, nb_cpu=nb_cpu, nb_gpu=nb_gpu, use_gpu=use_gpu)
