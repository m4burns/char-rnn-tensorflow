# Copyright (C) 2017 by Akira TAMAMORI
#
# This program is free software; you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE.  See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License along with
# this program.  If not, see <http://www.gnu.org/licenses/>.

# Copyright 2015 The TensorFlow Authors. All Rights Reserved.
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Notice:
# This file is tested on TensorFlow v0.12.0 only.

import numpy as np
import tensorflow as tf

from tensorflow.contrib import rnn

# Thanks to 'initializers_enhanced.py' of Project RNN Enhancement:
# https://github.com/nicolas-ivanov/Seq2Seq_Upgrade_TensorFlow/blob/master/rnn_enhancement/initializers_enhanced.py
def orthogonal_initializer(scale=1.0):
    def _initializer(shape, dtype=tf.float32):
        flat_shape = (shape[0], np.prod(shape[1:]))
        a = np.random.normal(0.0, 1.0, flat_shape)
        u, _, v = np.linalg.svd(a, full_matrices=False)
        q = u if u.shape == flat_shape else v
        q = q.reshape(shape)
        return tf.constant(scale * q[:shape[0], :shape[1]], dtype=tf.float32)
    return _initializer


class MultiplicativeLSTMCell(rnn.RNNCell):
    """Multiplicative LSTM.

       Ben Krause, Liang Lu, Iain Murray, and Steve Renals,
       "Multiplicative LSTM for sequence modelling, "
       in Workshop Track of ICLA 2017,
       https://openreview.net/forum?id=SJCS5rXFl&noteId=SJCS5rXFl

    """

    def __init__(self, num_units,
                 use_peepholes=False,
                 cell_clip=None,
                 initializer=orthogonal_initializer(),
                 num_proj=None,
                 proj_clip=None,
                 forget_bias=1.0,
                 state_is_tuple=True,
                 activation=tf.tanh):
        """Initialize the parameters for an LSTM cell.
        Args:
          num_units: int, The number of units in the LSTM cell.
          use_peepholes: bool, set True to enable diagonal/peephole
            connections.
          cell_clip: (optional) A float value, if provided the cell state
            is clipped by this value prior to the cell output activation.
          initializer: (optional) The initializer to use for the weight
            matrices.
          num_proj: (optional) int, The output dimensionality for
            the projection matrices.  If None, no projection is performed.
          forget_bias: Biases of the forget gate are initialized by default
            to 1 in order to reduce the scale of forgetting at the beginning of
            the training.
          activation: Activation function of the inner states.
        """
        self.num_units = num_units
        self.use_peepholes = use_peepholes
        self.cell_clip = cell_clip
        self.num_proj = num_proj
        self.proj_clip = proj_clip
        self.initializer = initializer
        self.forget_bias = forget_bias
        self.state_is_tuple = state_is_tuple
        self.activation = activation

        if num_proj:
            self._state_size = (
                tf.contrib.rnn.LSTMStateTuple(num_units, num_proj)
                if state_is_tuple else num_units + num_proj)
            self._output_size = num_proj
        else:
            self._state_size = (
                tf.contrib.rnn.LSTMStateTuple(num_units, num_units)
                if state_is_tuple else 2 * num_units)
            self._output_size = num_units

    @property
    def state_size(self):
        return self._state_size

    @property
    def output_size(self):
        return self._output_size

    def __call__(self, inputs, state, scope=None):

        num_proj = self.num_units if self.num_proj is None else self.num_proj

        if self.state_is_tuple:
            (c_prev, h_prev) = state
        else:
            c_prev = tf.slice(state, [0, 0], [-1, self.num_units])
            h_prev = tf.slice(state, [0, self.num_units], [-1, num_proj])

        dtype = inputs.dtype
        input_size = inputs.get_shape().with_rank(2)[1]

        with tf.variable_scope(scope or type(self).__name__):
            if input_size.value is None:
                raise ValueError(
                    "Could not infer input size from inputs.get_shape()[-1]")

            with tf.variable_scope("Multipli_Weight"):
                concat = _linear([inputs, h_prev], 2 * self.num_units, True)
            Wx, Wh = tf.split(concat, 2, axis=1)
            m = Wx * Wh  # equation (18)

            with tf.variable_scope("LSTM_Weight"):
                lstm_matrix = _linear([inputs, m], 4 * self.num_units, True)
            i, j, f, o = tf.split(lstm_matrix, 4, axis=1)

            # Diagonal connections
            if self.use_peepholes:
                w_f_diag = tf.get_variable(
                    "W_F_diag", shape=[self.num_units], dtype=dtype)
                w_i_diag = tf.get_variable(
                    "W_I_diag", shape=[self.num_units], dtype=dtype)
                w_o_diag = tf.get_variable(
                    "W_O_diag", shape=[self.num_units], dtype=dtype)

            if self.use_peepholes:
                c = c_prev * tf.sigmoid(f + self.forget_bias +
                                        w_f_diag * c_prev) + \
                    tf.sigmoid(i + w_i_diag * c_prev) * j
            else:
                c = c_prev * tf.sigmoid(f + self.forget_bias) + \
                    tf.sigmoid(i) * j

            if self.cell_clip is not None:
                c = tf.clip_by_value(c, -self.cell_clip, self.cell_clip)

            if self.use_peepholes:
                h = tf.sigmoid(o + w_o_diag * c) * \
                    self.activation(c * (o + w_o_diag * c))
            else:
                h = self.activation(c * o)

            if self.num_proj is not None:
                w_proj = tf.get_variable(
                    "W_P", [self.num_units, num_proj], dtype=dtype)

                h = tf.matmul(h, w_proj)
                if self.proj_clip is not None:
                    h = tf.clip_by_value(h, -self.proj_clip, self.proj_clip)

            new_state = (tf.contrib.rnn.LSTMStateTuple(c, h)
                         if self.state_is_tuple else tf.concat([c, h], 1))

            return h, new_state


def _linear(args, output_size, bias, bias_start=0.0, scope=None):
    """Linear map: sum_i(args[i] * W[i]), where W[i] is a variable.
    Args:
      args: a 2D Tensor or a list of 2D, batch x n, Tensors.
      output_size: int, second dimension of W[i].
      bias: boolean, whether to add a bias term or not.
      bias_start: starting value to initialize the bias; 0 by default.
      scope: VariableScope for the created subgraph; defaults to "Linear".
    Returns:
      A 2D Tensor with shape [batch x output_size] equal to
      sum_i(args[i] * W[i]), where W[i]s are newly created matrices.
    Raises:
      ValueError: if some of the arguments has unspecified or wrong shape.
    """
    if args is None or (isinstance(args, (list, tuple)) and not args):
        raise ValueError("`args` must be specified")
    if not isinstance(args, (list, tuple)):
        args = [args]

    # Calculate the total size of arguments on dimension 1.
    total_arg_size = 0
    shapes = [a.get_shape().as_list() for a in args]
    for shape in shapes:
        if len(shape) != 2:
            raise ValueError(
                "Linear is expecting 2D arguments: %s" % str(shapes))
        if not shape[1]:
            raise ValueError(
                "Linear expects shape[1] of arguments: %s" % str(shapes))
        else:
            total_arg_size += shape[1]

    # Now the computation.
    with tf.variable_scope(scope or "Linear"):
        matrix = tf.get_variable("NormalColMatrix", [total_arg_size, output_size])
        scalars = tf.get_variable("Scalars", [output_size], initializer=tf.constant_initializer(1.0))
        inv_norms = 1.0 / tf.norm(matrix, axis=0)
        scaled_norms = scalars * inv_norms
        scaled_matrix = tf.multiply(matrix, scaled_norms)

        if len(args) == 1:
            res = tf.matmul(args[0], scaled_matrix)
        else:
            res = tf.matmul(tf.concat(args, 1), scaled_matrix)
        if not bias:
            return res
        bias_term = tf.get_variable(
            "Bias", [output_size],
            initializer=tf.constant_initializer(bias_start))
    return res + bias_term
