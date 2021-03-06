import pandas as pd
import numpy as np
import tensorflow as tf

class MiniBatchFeeder(object):
    def __init__(self,
                 data,
                 batch_size=1000,
                 shuffle_data=False):

        self.data = data
        self.size = data.shape[0]
        self.batch_size = batch_size
        self.counter = 0
        self.shuffle_data = shuffle_data
        self.reset_counter()

    def _shuffle(self, seed=42):
        self.data = self.data[np.random.permutation(self.size), :]

    def __iter__(self):
        return self

    def reset_counter(self, start=0):
        self.counter = start
        if self.shuffle_data:
            self._shuffle()

    def next(self):
        if self.counter < self.size:
            low = self.counter
            high = self.counter + self.batch_size
        
            self.counter += self.batch_size

            return self.data[low : high, :]
        else:
            raise StopIteration

class CollaborativeFilter(object):

    def __init__(self,
                 weight_initializer,
                 num_user,
                 num_item,
                 num_dim=20,
                 num_epoch=100,
                 device="/gpu:0",
                 learning_rate=0.001,
                 reg=0.05):

        self.weight_initializer = weight_initializer 
        self.num_user = num_user
        self.num_item = num_item
        self.num_dim = num_dim
        self.num_epoch = num_epoch
        self.device = device
        self.learning_rate = learning_rate
        self.reg = reg

        self.embds_ = dict() 
        self.log_rmse_ = dict()

    def _create_param_tensors(self):
        with tf.device("/cpu:0"):
            bias = tf.get_variable(name="bias", shape=[], initializer=self.weight_initializer)
            bias_user = tf.get_variable(name="bias_user", shape=[self.num_user], initializer=self.weight_initializer)
            bias_item = tf.get_variable(name="bias_item", shape=[self.num_item], initializer=self.weight_initializer)
            embd_user = tf.get_variable(name="embd_user", shape=[self.num_user, self.num_dim], initializer=self.weight_initializer)
            embd_item = tf.get_variable(name="embd_item", shape=[self.num_item, self.num_dim], initializer=self.weight_initializer)

        return bias, bias_user, bias_item, embd_user, embd_item


    def _create_op_tensors(self, user_batch_index, item_batch_index, rating_batch, bias, bias_user, bias_item, embd_user, embd_item):

        rating_pred, (bias_user_batch, bias_item_batch, embd_user_batch, embd_item_batch) = self._create_pred_tensor(user_batch_index, item_batch_index, bias, bias_user, bias_item, embd_user, embd_item)

        global_step = tf.train.get_global_step()
        assert global_step is not None

        with tf.device(self.device):
            
            loss_error = tf.nn.l2_loss(tf.subtract(rating_pred, rating_batch))

            regularizer = tf.add(tf.nn.l2_loss(embd_user_batch), tf.nn.l2_loss(embd_item_batch))
            regularizer = tf.add(regularizer, tf.nn.l2_loss(bias_user_batch))
            regularizer = tf.add(regularizer, tf.nn.l2_loss(bias_item_batch), name="regularizer")

            loss_reg = tf.multiply(regularizer, tf.constant(self.reg))

            loss = tf.add(loss_error, loss_reg)
            train_op = tf.train.AdamOptimizer(self.learning_rate).minimize(loss, global_step=global_step)

        return train_op
            
    def _create_pred_tensor(self, user_batch_index, item_batch_index, bias, bias_user, bias_item, embd_user, embd_item):
        with tf.device(self.device):
            bias_user_batch = tf.nn.embedding_lookup(params=bias_user, ids=user_batch_index, name="bias_user_batch")
            bias_item_batch = tf.nn.embedding_lookup(params=bias_item, ids=item_batch_index, name="bias_item_batch")

            embd_user_batch = tf.nn.embedding_lookup(params=embd_user, ids=user_batch_index, name="embd_user_batch")
            embd_item_batch = tf.nn.embedding_lookup(params=embd_item, ids=item_batch_index, name="embd_item_batch")

            rating_pred = tf.reduce_sum(tf.multiply(embd_user_batch, embd_item_batch), 1)
            rating_pred = tf.add(rating_pred, bias)
            rating_pred = tf.add(rating_pred, bias_user_batch)
            rating_pred = tf.add(rating_pred, bias_item_batch, name="rating_pred") 

        return rating_pred, (bias_user_batch, bias_item_batch, embd_user_batch, embd_item_batch)

    def fit(self, train_data, test_data=None):

        bias, bias_user, bias_item, embd_user, embd_item = self._create_param_tensors()

        user_batch_index = tf.placeholder(tf.int32, name="user_batch_index")
        item_batch_index = tf.placeholder(tf.int32, name="item_batch_index")
        rating_batch = tf.placeholder(tf.float32, name="rating_batch")
        
        global_step = tf.contrib.framework.get_or_create_global_step()


        rating_pred, _ = self._create_pred_tensor(user_batch_index, item_batch_index, bias, bias_user, bias_item, embd_user, embd_item)
        train_op = self._create_op_tensors(user_batch_index, item_batch_index, rating_batch, bias, bias_user, bias_item, embd_user, embd_item)

        init_op = tf.global_variables_initializer()

        train_rmse, test_rmse = [], []

        with tf.Session() as sess:
            sess.run(init_op)
            for i in range(self.num_epoch):
#                print i

                train_data.reset_counter()
                for batch in train_data:
                    sess.run(train_op, feed_dict={user_batch_index: batch[:, 0],
                                                                 item_batch_index: batch[:, 1],
                                                                 rating_batch: batch[:, 2]})

                train_pred, train_true = self._eval(sess, rating_pred, train_data, user_batch_index, item_batch_index, with_target=True)
                train_rmse.append(np.sqrt(np.mean(np.square(train_pred - train_true))))

                if test_data is not None:
                    test_pred, test_true = self._eval(sess, rating_pred, test_data, user_batch_index, item_batch_index, with_target=True)
                    test_rmse.append(np.sqrt(np.mean(np.square(test_pred - test_true))))

            self.log_rmse_["train_rmse"] = np.array(train_rmse)
            if test_data is not None:
                self.log_rmse_["test_rmse"] = np.array(test_rmse)

            self.embds_ = dict(zip(["bias", "bias_user", "bias_item", "embd_user", "embd_item"],
                               sess.run([bias, bias_user, bias_item, embd_user, embd_item])))

        tf.reset_default_graph() 

    def predict(self, test_data):
        pred = []
        true = []

        test_data.reset_counter()
        for batch in test_data:
            user_ids = batch[:, 0]
            item_ids = batch[:, 1]

            bias = self.embds_["bias"]
            bias_user = self.embds_["bias_user"][user_ids]
            bias_item = self.embds_["bias_item"][item_ids]

            embd_user = self.embds_["embd_user"][user_ids, :]
            embd_item = self.embds_["embd_item"][item_ids, :]


            pred.extend(list(np.sum(embd_user * embd_item, axis=1) + bias_user + bias_item + bias))
            true.extend(list(batch[:, 2]))

        pred = np.clip(np.array(pred), 1.0, 5.0)
        true = np.array(true)
#        print "%f" % np.sqrt(np.mean(np.square(pred - true)))

        return pred, true

    def _eval(self, sess, rating_pred, data, user_batch_index, item_batch_index, with_target=False):
        pred = []
        true = []
 
        data.reset_counter()
        for batch in data:
            y_ = sess.run(rating_pred, feed_dict={user_batch_index: batch[:, 0],
                                                  item_batch_index: batch[:, 1]})
            pred.extend(y_)
            if with_target:
                true.extend(list(batch[:, 2]))        

        pred = np.clip(np.array(pred), 1.0, 5.0)
        true = np.array(true)
        return pred, true
        
