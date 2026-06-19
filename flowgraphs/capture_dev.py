#!/usr/bin/env python3
# -*- coding: utf-8 -*-

#
# SPDX-License-Identifier: GPL-3.0
#
# GNU Radio Python Flow Graph
# Title: gr-dis Dev Capture
# Author: gr-dis dev
# Description: Dual-channel WBFM dev capture - publishes to the gr-dis bridge over ZMQ. See flowgraphs/README.md for usage.
# GNU Radio version: 3.10.11.0

from PyQt5 import Qt
from gnuradio import qtgui
from PyQt5 import QtCore
from gnuradio import analog
from gnuradio import blocks
from gnuradio import filter
from gnuradio.filter import firdes
from gnuradio import gr
from gnuradio.fft import window
import sys
import signal
from PyQt5 import Qt
from argparse import ArgumentParser
from gnuradio.eng_arg import eng_float, intx
from gnuradio import eng_notation
from gnuradio import soapy
from gr_dis.engine.zmq_sink import make_zmq_audio_sink
import threading



class capture_dev(gr.top_block, Qt.QWidget):

    def __init__(self):
        gr.top_block.__init__(self, "gr-dis Dev Capture", catch_exceptions=True)
        Qt.QWidget.__init__(self)
        self.setWindowTitle("gr-dis Dev Capture")
        qtgui.util.check_set_qss()
        try:
            self.setWindowIcon(Qt.QIcon.fromTheme('gnuradio-grc'))
        except BaseException as exc:
            print(f"Qt GUI: Could not set Icon: {str(exc)}", file=sys.stderr)
        self.top_scroll_layout = Qt.QVBoxLayout()
        self.setLayout(self.top_scroll_layout)
        self.top_scroll = Qt.QScrollArea()
        self.top_scroll.setFrameStyle(Qt.QFrame.NoFrame)
        self.top_scroll_layout.addWidget(self.top_scroll)
        self.top_scroll.setWidgetResizable(True)
        self.top_widget = Qt.QWidget()
        self.top_scroll.setWidget(self.top_widget)
        self.top_layout = Qt.QVBoxLayout(self.top_widget)
        self.top_grid_layout = Qt.QGridLayout()
        self.top_layout.addLayout(self.top_grid_layout)

        self.settings = Qt.QSettings("gnuradio/flowgraphs", "capture_dev")

        try:
            geometry = self.settings.value("geometry")
            if geometry:
                self.restoreGeometry(geometry)
        except BaseException as exc:
            print(f"Qt GUI: Could not restore geometry: {str(exc)}", file=sys.stderr)
        self.flowgraph_started = threading.Event()

        ##################################################
        # Variables
        ##################################################
        self.zmq_endpoint = zmq_endpoint = "tcp://127.0.0.1:5555"
        self.squelch_db = squelch_db = -90
        self.samp_rate = samp_rate = 2400000
        self.rf_freq_2 = rf_freq_2 = 106.9e6
        self.rf_freq_1 = rf_freq_1 = 107.7e6
        self.gain_db = gain_db = 20
        self.channel_id_2 = channel_id_2 = "fm_ch2"
        self.channel_id_1 = channel_id_1 = "fm_ch1"
        self.center_freq = center_freq = 107.3e6
        self.audio_rate = audio_rate = int(48e3)

        ##################################################
        # Blocks
        ##################################################

        self._squelch_db_range = qtgui.Range(-100, 0, 1, -90, 200)
        self._squelch_db_win = qtgui.RangeWidget(self._squelch_db_range, self.set_squelch_db, "Squelch (dBFS)", "counter_slider", float, QtCore.Qt.Horizontal)
        self.top_grid_layout.addWidget(self._squelch_db_win, 2, 0, 1, 1)
        for r in range(2, 3):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(0, 1):
            self.top_grid_layout.setColumnStretch(c, 1)
        self._rf_freq_2_range = qtgui.Range(center_freq-(samp_rate/2), center_freq+(samp_rate/2), 100e3, 106.9e6, 200)
        self._rf_freq_2_win = qtgui.RangeWidget(self._rf_freq_2_range, self.set_rf_freq_2, "RF 2 Frequency (Hz)", "counter_slider", float, QtCore.Qt.Horizontal)
        self.top_grid_layout.addWidget(self._rf_freq_2_win, 1, 0, 1, 2)
        for r in range(1, 2):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(0, 2):
            self.top_grid_layout.setColumnStretch(c, 1)
        self._rf_freq_1_range = qtgui.Range(center_freq-(samp_rate/2), center_freq+(samp_rate/2), 100e3, 107.7e6, 200)
        self._rf_freq_1_win = qtgui.RangeWidget(self._rf_freq_1_range, self.set_rf_freq_1, "RF 1 Frequency (Hz)", "counter_slider", float, QtCore.Qt.Horizontal)
        self.top_grid_layout.addWidget(self._rf_freq_1_win, 0, 0, 1, 2)
        for r in range(0, 1):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(0, 2):
            self.top_grid_layout.setColumnStretch(c, 1)
        self._gain_db_range = qtgui.Range(0, 50, 1, 20, 200)
        self._gain_db_win = qtgui.RangeWidget(self._gain_db_range, self.set_gain_db, "Gain (dB)", "counter_slider", float, QtCore.Qt.Horizontal)
        self.top_grid_layout.addWidget(self._gain_db_win, 2, 1, 1, 1)
        for r in range(2, 3):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(1, 2):
            self.top_grid_layout.setColumnStretch(c, 1)
        self.soapy_rtlsdr_source_0 = None
        dev = 'driver=rtlsdr'
        stream_args = 'bufflen=16384'
        tune_args = ['']
        settings = ['']

        def _set_soapy_rtlsdr_source_0_gain_mode(channel, agc):
            self.soapy_rtlsdr_source_0.set_gain_mode(channel, agc)
            if not agc:
                  self.soapy_rtlsdr_source_0.set_gain(channel, self._soapy_rtlsdr_source_0_gain_value)
        self.set_soapy_rtlsdr_source_0_gain_mode = _set_soapy_rtlsdr_source_0_gain_mode

        def _set_soapy_rtlsdr_source_0_gain(channel, name, gain):
            self._soapy_rtlsdr_source_0_gain_value = gain
            if not self.soapy_rtlsdr_source_0.get_gain_mode(channel):
                self.soapy_rtlsdr_source_0.set_gain(channel, gain)
        self.set_soapy_rtlsdr_source_0_gain = _set_soapy_rtlsdr_source_0_gain

        def _set_soapy_rtlsdr_source_0_bias(bias):
            if 'biastee' in self._soapy_rtlsdr_source_0_setting_keys:
                self.soapy_rtlsdr_source_0.write_setting('biastee', bias)
        self.set_soapy_rtlsdr_source_0_bias = _set_soapy_rtlsdr_source_0_bias

        self.soapy_rtlsdr_source_0 = soapy.source(dev, "fc32", 1, '',
                                  stream_args, tune_args, settings)

        self._soapy_rtlsdr_source_0_setting_keys = [a.key for a in self.soapy_rtlsdr_source_0.get_setting_info()]

        self.soapy_rtlsdr_source_0.set_sample_rate(0, samp_rate)
        self.soapy_rtlsdr_source_0.set_frequency(0, center_freq)
        self.soapy_rtlsdr_source_0.set_frequency_correction(0, 0)
        self.set_soapy_rtlsdr_source_0_bias(bool(False))
        self._soapy_rtlsdr_source_0_gain_value = gain_db
        self.set_soapy_rtlsdr_source_0_gain_mode(0, bool(False))
        self.set_soapy_rtlsdr_source_0_gain(0, 'TUNER', gain_db)
        self.rational_resampler_xxx_0_0 = filter.rational_resampler_fff(
                interpolation=1,
                decimation=(int(audio_rate/8000)),
                taps=[],
                fractional_bw=0.4)
        self.rational_resampler_xxx_0 = filter.rational_resampler_fff(
                interpolation=1,
                decimation=(int(audio_rate/8000)),
                taps=[],
                fractional_bw=0.4)
        self.freq_xlating_fir_filter_ccc_0_0_0 = filter.freq_xlating_fir_filter_ccf(1, firdes.low_pass(1.0, samp_rate, 100000, 25000), (rf_freq_2 - center_freq), samp_rate)
        self.freq_xlating_fir_filter_ccc_0_0 = filter.freq_xlating_fir_filter_ccf(1, firdes.low_pass(1.0, samp_rate, 100000, 25000), (rf_freq_1 - center_freq), samp_rate)
        self.gr_dis_zmq_audio_sink_0_0 = make_zmq_audio_sink(zmq_endpoint, channel_id_2, chain_name="wfm", rf_freq_hz=int(rf_freq_2), channel_bandwidth_hz=200000, squelch_rms_threshold=50.0, chain_config={})
        self.gr_dis_zmq_audio_sink_0 = make_zmq_audio_sink(zmq_endpoint, channel_id_1, chain_name="wfm", rf_freq_hz=int(rf_freq_1), channel_bandwidth_hz=200000, squelch_rms_threshold=50.0, chain_config={})
        self.blocks_multiply_const_vxx_0_0 = blocks.multiply_const_ff(32767)
        self.blocks_multiply_const_vxx_0 = blocks.multiply_const_ff(32767)
        self.blocks_float_to_short_0_0 = blocks.float_to_short(1, 1)
        self.blocks_float_to_short_0 = blocks.float_to_short(1, 1)
        self.analog_wfm_rcv_0_0 = analog.wfm_rcv(
        	quad_rate=samp_rate,
        	audio_decimation=(int(samp_rate/audio_rate)),
        )
        self.analog_wfm_rcv_0 = analog.wfm_rcv(
        	quad_rate=samp_rate,
        	audio_decimation=(int(samp_rate/audio_rate)),
        )
        self.analog_pwr_squelch_cc_0_0 = analog.pwr_squelch_cc(squelch_db, (1e-3), 2400, False)
        self.analog_pwr_squelch_cc_0 = analog.pwr_squelch_cc(squelch_db, (1e-3), 2400, False)


        ##################################################
        # Connections
        ##################################################
        self.connect((self.analog_pwr_squelch_cc_0, 0), (self.analog_wfm_rcv_0, 0))
        self.connect((self.analog_pwr_squelch_cc_0_0, 0), (self.analog_wfm_rcv_0_0, 0))
        self.connect((self.analog_wfm_rcv_0, 0), (self.rational_resampler_xxx_0, 0))
        self.connect((self.analog_wfm_rcv_0_0, 0), (self.rational_resampler_xxx_0_0, 0))
        self.connect((self.blocks_float_to_short_0, 0), (self.gr_dis_zmq_audio_sink_0, 0))
        self.connect((self.blocks_float_to_short_0_0, 0), (self.gr_dis_zmq_audio_sink_0_0, 0))
        self.connect((self.blocks_multiply_const_vxx_0, 0), (self.blocks_float_to_short_0, 0))
        self.connect((self.blocks_multiply_const_vxx_0_0, 0), (self.blocks_float_to_short_0_0, 0))
        self.connect((self.freq_xlating_fir_filter_ccc_0_0, 0), (self.analog_pwr_squelch_cc_0, 0))
        self.connect((self.freq_xlating_fir_filter_ccc_0_0_0, 0), (self.analog_pwr_squelch_cc_0_0, 0))
        self.connect((self.rational_resampler_xxx_0, 0), (self.blocks_multiply_const_vxx_0, 0))
        self.connect((self.rational_resampler_xxx_0_0, 0), (self.blocks_multiply_const_vxx_0_0, 0))
        self.connect((self.soapy_rtlsdr_source_0, 0), (self.freq_xlating_fir_filter_ccc_0_0, 0))
        self.connect((self.soapy_rtlsdr_source_0, 0), (self.freq_xlating_fir_filter_ccc_0_0_0, 0))


    def closeEvent(self, event):
        self.settings = Qt.QSettings("gnuradio/flowgraphs", "capture_dev")
        self.settings.setValue("geometry", self.saveGeometry())
        self.stop()
        self.wait()

        event.accept()

    def get_zmq_endpoint(self):
        return self.zmq_endpoint

    def set_zmq_endpoint(self, zmq_endpoint):
        self.zmq_endpoint = zmq_endpoint

    def get_squelch_db(self):
        return self.squelch_db

    def set_squelch_db(self, squelch_db):
        self.squelch_db = squelch_db
        self.analog_pwr_squelch_cc_0.set_threshold(self.squelch_db)
        self.analog_pwr_squelch_cc_0_0.set_threshold(self.squelch_db)

    def get_samp_rate(self):
        return self.samp_rate

    def set_samp_rate(self, samp_rate):
        self.samp_rate = samp_rate
        self.freq_xlating_fir_filter_ccc_0_0.set_taps(firdes.low_pass(1.0, self.samp_rate, 100000, 25000))
        self.freq_xlating_fir_filter_ccc_0_0_0.set_taps(firdes.low_pass(1.0, self.samp_rate, 100000, 25000))
        self.soapy_rtlsdr_source_0.set_sample_rate(0, self.samp_rate)

    def get_rf_freq_2(self):
        return self.rf_freq_2

    def set_rf_freq_2(self, rf_freq_2):
        self.rf_freq_2 = rf_freq_2
        self.freq_xlating_fir_filter_ccc_0_0_0.set_center_freq((self.rf_freq_2 - self.center_freq))
        self.gr_dis_zmq_audio_sink_0_0.set_rf_freq_hz(int(self.rf_freq_2))

    def get_rf_freq_1(self):
        return self.rf_freq_1

    def set_rf_freq_1(self, rf_freq_1):
        self.rf_freq_1 = rf_freq_1
        self.freq_xlating_fir_filter_ccc_0_0.set_center_freq((self.rf_freq_1 - self.center_freq))
        self.gr_dis_zmq_audio_sink_0.set_rf_freq_hz(int(self.rf_freq_1))

    def get_gain_db(self):
        return self.gain_db

    def set_gain_db(self, gain_db):
        self.gain_db = gain_db
        self.set_soapy_rtlsdr_source_0_gain(0, 'TUNER', self.gain_db)

    def get_channel_id_2(self):
        return self.channel_id_2

    def set_channel_id_2(self, channel_id_2):
        self.channel_id_2 = channel_id_2

    def get_channel_id_1(self):
        return self.channel_id_1

    def set_channel_id_1(self, channel_id_1):
        self.channel_id_1 = channel_id_1

    def get_center_freq(self):
        return self.center_freq

    def set_center_freq(self, center_freq):
        self.center_freq = center_freq
        self.freq_xlating_fir_filter_ccc_0_0.set_center_freq((self.rf_freq_1 - self.center_freq))
        self.freq_xlating_fir_filter_ccc_0_0_0.set_center_freq((self.rf_freq_2 - self.center_freq))
        self.soapy_rtlsdr_source_0.set_frequency(0, self.center_freq)

    def get_audio_rate(self):
        return self.audio_rate

    def set_audio_rate(self, audio_rate):
        self.audio_rate = audio_rate




def main(top_block_cls=capture_dev, options=None):

    qapp = Qt.QApplication(sys.argv)

    tb = top_block_cls()

    tb.start()
    tb.flowgraph_started.set()

    tb.show()

    def sig_handler(sig=None, frame=None):
        tb.stop()
        tb.wait()

        Qt.QApplication.quit()

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    timer = Qt.QTimer()
    timer.start(500)
    timer.timeout.connect(lambda: None)

    qapp.exec_()

if __name__ == '__main__':
    main()
